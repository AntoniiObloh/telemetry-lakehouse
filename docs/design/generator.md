# Design: `generator/` package (spec section 3.1)

## Context
The generator is the data source for the whole lakehouse. Five devices from
`generator/devices.csv` report telemetry every 30 s. Raw 5-minute JSONL batches with five
deliberate data defects go to `data/raw/...` (local) or to S3 `RAW_BUCKET`; bronze and dbt
clean them downstream. Separately, a clean live stream goes to ThingsBoard over MQTT.
Output must be reproducible from `--seed`, and every defect is covered by pytest.

## Spec (section 3.1)
| Parameter | Value |
|---|---|
| Reading rate | every 30 s per device |
| Batch | 5 min → `data/raw/telemetry/date=YYYY-MM-DD/batch_<sent_at>.jsonl` locally, or the same key in S3 |
| Duplicates | `--dup-rate 0.02`: the event repeats in the same or the next batch with the same `event_id` |
| Late events | `--late-rate 0.01`: the event lands in a batch 1–48 h later (`ts` old, `sent_at` new) |
| Schema drift | `--drift-from 2026-10-08`: from this date the field `firmware` (`"1.3.0"`) appears |
| Gaps | `--gap-device sensor-03 --gap-hours 3`: the device is silent |
| Outliers | `--outlier-rate 0.005`: `temperature` = 999 or −80 |
| Reproducibility | `--seed 42`: the same input gives the same files |

## Decisions
- **Modes:** `batch` (file/s3, simulated time over `--start..--end`, deterministic) and
  `live` (MQTT only, wall-clock time, one point every 30 s).
- **MQTT carries no defects:** clean readings; ThingsBoard gets only
  `temperature/humidity/battery`. Fixing defects is the lakehouse's job; cleaned gold reaches
  ThingsBoard through the publisher.
- **Time:** `ts` and `sent_at` are epoch milliseconds (UTC), like ThingsBoard. File names use
  a compact UTC form, `batch_20261008T120500Z.jsonl`, because Windows file names cannot
  contain colons.
- **Event fields:** `event_id, device_id, ts, sent_at, temperature, humidity, battery`, plus
  `firmware` from the drift date. `site` and `model` live only in `devices.csv` (a dbt seed),
  not in every event.
- **Gap start:** `--gap-start`; if omitted, the start is picked from the seed inside the
  window and printed in the run report.
- **The spec values are the defaults:** all five defects are on unless disabled
  (rate 0, `--gap-hours 0`, `--drift-from none`, or `--no-defects` for all of them).
- **S3 sink now**, tested with `botocore.stub.Stubber` (no AWS calls). `RAW_BUCKET` appears
  in Stage 4; until then only `file` and `mqtt` are used.

## Layout
```
generator/
  __init__.py   # GeneratorError
  __main__.py   # argparse: batch | live subcommands, run report, exit codes
  devices.py    # Device dataclass + load_devices(csv) with validation
  readings.py   # clean signal: per-event RNG, event_id, temperature/humidity/battery
  batch.py      # pure core, no I/O: DefectConfig, simulate(...) -> SimulationResult
  sinks.py      # FileSink, S3Sink: write(batch)
  live.py       # load_tokens + real-time MQTT publishing
tests/
  conftest.py   # session fixture: 7 days, seed 42, default defects
  test_defects.py · test_batches.py · test_sinks.py · test_live.py
```
`pyproject.toml` sets `[tool.pytest.ini_options] testpaths = ["tests"]` and
`pythonpath = ["."]`: the project is virtual (no build system), so `import generator` from
`tests/` needs the repo root on the path. No new dependencies: `boto3`, `paho-mqtt` and
`pytest` are already in the project.

## Core (`batch.py`, `readings.py`)
- **Slots:** each device has a `ts` every 30 s. A `Batch(sent_at)` exists for every 5-minute
  boundary in `(start, end]`; an on-time event goes to the batch with
  `sent_at - 5 min <= ts < sent_at`.
- **Per-event RNG:** `random.Random(f"{seed}:{device_id}:{ts}")`. String seeds are hashed with
  SHA-512, and only `Random.random()` is used, the one method Python guarantees to stay
  reproducible across versions. Draws happen in a fixed order: reading noise → outlier →
  late → duplicate. They happen even when a rate is 0, so changing `--dup-rate` does not
  change which events become outliers.
- **`event_id`:** `uuid5(namespace, f"{seed}:{device_id}:{ts}")`, stable across runs and
  Python versions; a duplicate is a copy, so it keeps the same id.
- **Batch content depends only on (seed, config, sent_at), not on the window.** Events are
  simulated from `start - 48 h 5 min`, so late events from before the window still arrive
  in it; anything due after `end` appears in the next run. Two consecutive runs equal one
  run over the combined window (given an explicit `--gap-start`).
- **Defects:**
  - *Duplicate* (`dup_rate`): a copy with the same `event_id`, `ts` and values, in the same or
    the next batch (50/50) relative to the arrival batch; `sent_at` is that batch's.
  - *Late* (`late_rate`): delay `uniform(1 h, 48 h)` → the first batch after `ts + delay`;
    `ts` stays old, `sent_at` is new.
  - *Drift*: `firmware: "1.3.0"` is added when `ts >= drift_from 00:00Z`. This goes by reading
    time, so a late event with `ts` before the date has no such field.
  - *Gap*: `gap_device` has no slots at all in `[gap_start, gap_start + gap_hours)`, hence no
    late events or duplicates from that interval either.
  - *Outlier* (`outlier_rate`): `temperature` = 999.0 or −80.0.
- **Readings:**
  - **temperature:** model baseline + daily sine + noise (σ 0.3), rounded to 0.01;
  - **humidity:** correlated with temperature, plus noise, clamped to 20–90;
  - **battery:** an integer that slowly drains with `ts`. It is a function of time, not state.

  Batch and live modes share these.
- **Byte determinism:** records in a batch are sorted by `(ts, device_id, event_id)`;
  `json.dumps(..., separators=(",", ":"))`; written as bytes with `\n`, so LF even on Windows.

## Sinks (`sinks.py`)
- Key: `raw/telemetry/date=YYYY-MM-DD/batch_YYYYMMDDTHHMMSSZ.jsonl`; `date` comes from
  `sent_at` (UTC).
- **FileSink:** `--out-dir` (default `<repo>/data`) + key. Writes are atomic (tmp file +
  `os.replace`, like `write_tokens` in `thingsboard/provision.py`). Re-running overwrites the
  same files with the same bytes. `data/` is in `.gitignore`.
- **S3Sink:** `put_object(Bucket=RAW_BUCKET, Key=key, ContentType="application/x-ndjson")`.
  The client can be injected for tests. A missing `RAW_BUCKET` fails fast with a clear
  message; AWS errors report only the error code. Credentials come from the default boto3
  chain, and nothing is printed.
- Assumption: the bucket prefix is `raw/` (locally, `data/` plays the bucket's role),
  matching "S3 raw/" in `CLAUDE.md`.

## Live (`live.py`)
- `load_tokens()` reads `thingsboard/devices.tokens.json` itself, following
  `thingsboard/smoke.py:load_tokens`. A device without a token is an error that names the
  device and never shows any value.
- One persistent paho client per device, with `username = token`. The client waits for
  CONNACK via `threading.Event` and publishes with QoS 1, waiting for PUBACK (the pattern
  from `thingsboard/smoke.py:send_points`). `reconnect_delay_set` adds automatic
  reconnects.
- Loop: wait for the next 30-s boundary → for each device the clean reading for that `ts` →
  `{"ts": ms, "values": {...}}` to `v1/devices/me/telemetry`. Prints one line per tick
  (`... sent 5/5`). Ctrl+C disconnects cleanly. Flags: `--host`, `--port`,
  `--duration-min` (optional).

## CLI
```
uv run python -m generator batch --sink file|s3 --start 2026-10-06 --end 2026-10-10
    [--seed 42] [--dup-rate 0.02] [--late-rate 0.01] [--drift-from 2026-10-08]
    [--gap-device sensor-03] [--gap-hours 3] [--gap-start 2026-10-07T13:30]
    [--outlier-rate 0.005] [--no-defects] [--out-dir data]
uv run python -m generator live [--seed 42] [--host localhost] [--port 1883] [--duration-min N]
```
- `--start` and `--end` are required: a date or a date-time, UTC only. Both must be
  aligned to 5 minutes, with `start < end`. Rates must be within 0..1, and `--gap-device`
  must exist in `devices.csv`.
- The batch report shows the window, seed, actual gap interval, the number of batches and
  events, and the counts of duplicates, late events and outliers. The exit code is 1 on
  error.

## Tests (`uv run pytest`, seed 42, window 2026-10-05..2026-10-12)
Rates are measured only over events with `ts` in `[start, end − 48 h 5 min)`, so the window
edges do not skew them. Relative tolerances: duplicates ±15 %, late ±20 %, outliers ±25 %.
For this sample that is at least 4σ, yet a rate that is off by an order of magnitude still
fails.
- **Duplicates:** the rate is within tolerance. Copies are identical except `sent_at`, and
  their `sent_at` values differ by 0 or 5 min.
- **Late events:** the rate is within tolerance, and `1 h <= sent_at − ts <= 48 h + 5 min`.
  On-time events have `0 < sent_at − ts <= 5 min`.
- **Drift:** no event with `ts < 2026-10-08` has `firmware`; every event with `ts` on or after
  it has `"1.3.0"`. The 23:59:30 / 00:00:00 boundary is checked.
- **Gap:** `sensor-03` has no events inside the gap, while the other devices have a full
  count there and `sensor-03` has events right before and after it. The seed-derived
  default start lies inside the window and is stable.
- **Outliers:** the rate is within tolerance; outlier values are only 999 or −80; all other
  temperatures are within 10..35.
- **`--no-defects`:** zero defects; cadence of 30 s (2880 events per device per day).
- **Reproducibility:** two FileSink runs with seed 42 into different `tmp_path` dirs are
  byte-identical; another seed differs.
- **Composability:** `simulate(a..c) == simulate(a..b) + simulate(b..c)`.
- **Key and format:** the key matches the pattern, `date=` matches `sent_at`, every line is
  valid JSON, and line endings are LF.
- **S3:** Stubber checks `Bucket/Key/Body/ContentType`; without `RAW_BUCKET` the sink fails.
- **Live:** the payload has only the three fields, and `load_tokens` errors never contain a
  token value (a fake token in `tmp_path`). There are no network tests.

## Verification
1. `uv run ruff check .`, `uv run ruff format` for the new files, `uv run pytest`.
2. `uv run python -m generator batch --sink file --start 2026-10-06 --end 2026-10-10`:
   check the report and spot-check 1–2 files in `data/raw/telemetry/` (synthetic data).
3. Re-run with the same seed: file hashes match (`Get-FileHash`).
4. `live` needs a running ThingsBoard and writes to it; `--sink s3` needs the Stage 4 bucket.
   Both are run manually.
