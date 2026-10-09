---
paths:
  - "generator/**"
---

# Generator rules

Design and rationale: `docs/design/generator.md`.

## Event format
- One compact JSON object per line, LF endings, UTF-8 (JSONL).
- Fields: `event_id` (uuid5 of `seed:device_id:ts`), `device_id`, `ts` and `sent_at`
  (epoch ms, UTC), `temperature`, `humidity`, `battery`; `firmware` (`"1.3.0"`) only
  when `ts` is on or after `--drift-from`.
- Batches every 5 minutes, key `raw/telemetry/date=YYYY-MM-DD/batch_YYYYMMDDTHHMMSSZ.jsonl`
  (date and file name from `sent_at`). File sink writes under `data/`, S3 sink to
  `RAW_BUCKET`.

## Defects (batch mode only)
- Duplicates `--dup-rate 0.02`: same `event_id`, same or next batch.
- Late events `--late-rate 0.01`: `sent_at` 1-48 h after `ts`.
- Schema drift `--drift-from 2026-10-08`: `firmware` appears.
- Gap `--gap-device sensor-03 --gap-hours 3`: no events at all. The gap start is derived
  per run from the seed and the window; pass `--gap-start` to keep one gap across runs.
- Outliers `--outlier-rate 0.005`: `temperature` 999 or -80.
- These defaults come from spec 3.1 and the dbt tests downstream expect them. Live MQTT
  mode stays clean (no defects).

## Determinism: do not touch the seed without a real need
- Same seed and arguments give byte-identical files (tests check it).
- Keep the default seed (42), the per-event RNG key (`seed:device_id:ts`), the draw order
  (`READING_DRAWS`, then outlier, late and duplicate draws) and the `event_id` namespace.
  Changing any of them changes every file, every `event_id` and every downstream count.
- `batch.py` stays pure (no I/O); files, S3 and MQTT live in `sinks.py` and `live.py`.

## Other
- `devices.csv` is copied to `dbt/seeds/devices.csv`; change both
  (`tests/test_dbt_seed.py` fails otherwise).
- Device tokens come from `thingsboard/devices.tokens.json`, read by the code only,
  never printed.
- Before done: `uv run ruff check .` and `uv run pytest`; keep `docs/design/generator.md`
  in sync.
