# Runbook

What to do when a scheduled job or an alert fires. Every entry has the same four parts:
**means**, **check**, **fix**, **roll back**.

Conventions:
- Cluster: kind `lakehouse`, context `kind-lakehouse`, namespace `lakehouse-dev`. Check the
  context first: `kubectl config current-context`.
- Run dbt, sqlfluff and the publisher from **PowerShell** (Git Bash rewrites
  `DATABRICKS_HTTP_PATH` and every connection fails with OpenSession 404). Run
  `kubectl patch` with JSON in **Git Bash** (PowerShell 5.1 strips the inner quotes).
- Long logs: ask the `lakehouse-toolkit:log-reader` agent ("розбери падіння <job>"); it
  answers in at most 10 lines.
- Destructive steps (delete, drop, restore, watermark reset) are marked **human**: do them
  yourself after reading what they change.

## Triage in one minute

```bash
kubectl -n lakehouse-dev get cronjobs,jobs,pods
kubectl -n lakehouse-dev logs job/<job-name> --tail=200
```

| Symptom | Most likely | Section |
|---|---|---|
| Fleet dashboard frozen, `gold_lag_min` growing | dbt-build or publisher failing, or bronze not ingesting | [dbt-build](#cronjob-dbt-build), [publisher](#cronjob-publisher), [freshness](#alert-dbt-source-freshness) |
| Hourly charts stop, live temperature fine | bronze job paused (dev mode) or generator-batch failing | [generator-batch](#cronjob-generator-batch), [freshness](#alert-dbt-source-freshness) |
| Live temperature stops | generator-live or ThingsBoard down | [generator-live](#deployment-generator-live) |
| Pod `CreateContainerConfigError` | a Secret is missing | the job's section, "Secrets" |
| Pod `ErrImageNeverPull` | the image is not loaded into kind | [Images](#images-build-load-roll-back) |

Pipeline order: `generator-batch` (every 5 min) -> S3 `raw/` -> Databricks job
`ingest_bronze` (minute 5, paused in the dev bundle: run it by hand) -> `dbt-build`
(minute 15) -> `publisher` (minute 30) -> ThingsBoard.

---

## CronJob generator-batch

`*/5 * * * *` UTC, image `generator:0.1.0`, Secret `generator-aws`, `RAW_BUCKET` from the
kind overlay.

**Means.** Each run writes the one batch that closed at the last 5-minute boundary to
`s3://<RAW_BUCKET>/raw/telemetry/date=YYYY-MM-DD/batch_<sent_at>.jsonl`, with the injected
defects (duplicates, late events, drift, outliers; no gap: `--gap-device none`). A failed or
skipped run means that batch is missing from raw/.

**Check.**
```bash
kubectl -n lakehouse-dev get jobs -l app.kubernetes.io/name=generator-batch
kubectl -n lakehouse-dev logs job/<generator-batch-...>
```
A good log ends with `batches  1 -> s3://...` and an `events` line. Common failures:
- `FAIL RAW_BUCKET is not set`: the overlay patch is missing (`kubectl kustomize
  k8s/overlays/kind | grep RAW_BUCKET`);
- `S3 put_object failed: AccessDenied` or `InvalidAccessKeyId`: the `generator-aws` keys
  (user `tl-generator`) are wrong or rotated;
- pod `CreateContainerConfigError`: Secret `generator-aws` missing.

**Fix.**
- Secret: recreate it as in `k8s/README.md`, then wait for the next run or start one:
  `kubectl -n lakehouse-dev create job --from=cronjob/generator-batch gb-manual-1`.
- Missing batches: regenerate the exact window from your machine (same seed, same bytes):
  `uv run python -m generator batch --sink s3 --start <from> --end <to> --gap-device none`
  (5-minute aligned UTC; the batches are `(start, end]`). Batches that already exist are
  overwritten with identical bytes, and Auto Loader does not re-read them.

**Roll back.**
- Stop it: `kubectl -n lakehouse-dev patch cronjob generator-batch -p '{"spec":{"suspend":true}}'`
  (Git Bash); `"suspend":false` resumes it.
- Bad code: roll back the image (see [Images](#images-build-load-roll-back)).
- Bad data already in S3 and bronze (**human**): delete the wrong objects under `raw/`
  and remove their rows from bronze with `DELETE FROM lakehouse.bronze.telemetry_raw WHERE
  _source_file LIKE '%<key>%'`; stg keeps first arrivals, so rebuild it with
  `dbt build -s stg_telemetry+ --full-refresh --target dev` afterwards.

## Databricks job ingest_bronze

Not a CronJob, but every CronJob downstream depends on it. Schedule `0 5 * * * ?` (minute
5), **paused** in the dev deployment of the bundle.

**Means.** Auto Loader appends new raw files to `lakehouse.bronze.telemetry_raw` and stops
(`availableNow`). If it does not run, dbt and the publisher keep republishing old data.

**Check.** Databricks UI -> Jobs -> `ingest_bronze` -> last runs; or the freshness check below.

**Fix.** Run it: from `databricks/`,
`databricks bundle run ingest_bronze -t dev --var bucket=<RAW_BUCKET>`. A run that fails
right after a new column (firmware) is expected once: `addNewColumns` stops the stream,
and the job's retry picks up the new schema.

**Roll back.** Deploy the previous bundle (`git checkout <commit> -- databricks/`, then
`databricks bundle deploy -t dev`). Bronze rows are append-only; removing rows is
**human** (see generator-batch).

## CronJob dbt-build

`15 * * * *` UTC, image `dbt-runner:0.1.0`, Secret `databricks-dbt`, `dbt build --target dev`.

**Means.** Rebuilds silver and gold in `lakehouse.dbt_dev` from bronze: stg_telemetry
(incremental), int, dim_device (snapshot), fct_device_hourly, mart_device_health,
srv_thingsboard. Failed tests stop the build; the three `warn` tests do not (see
[dbt warn tests](#alert-dbt-warn-tests)).

**Check.**
```bash
kubectl -n lakehouse-dev logs job/<dbt-build-...> --tail=200
```
The last line is `Done. PASS=.. WARN=.. ERROR=..`. Typical failures:
- `BAD_REQUEST: Cannot create the resource, please try again later` on OpenSession: the
  serverless warehouse did not start (Free Edition capacity). Transient: the next run or a
  manual one usually works;
- `UNRESOLVED_COLUMN` or `TABLE_OR_VIEW_NOT_FOUND`: a model or its source changed; reproduce
  locally with `uv run dbt build -s <model>+ --project-dir dbt --profiles-dir dbt --target dev`;
- a failing test (`FAIL n`): real data problem or a broken model; the test name says which;
- `Env var required but not provided`: Secret `databricks-dbt` incomplete.

**Fix.** Transient: `kubectl -n lakehouse-dev create job --from=cronjob/dbt-build dbt-manual-1`.
Model bug: fix on a branch, build locally on dev, merge; then rebuild and load the
`dbt-runner` image (the project is baked into it).

**Roll back.**
- Image: previous `dbt-runner` (see [Images](#images-build-load-roll-back)); tables are
  rebuilt from bronze on the next run, except the two below.
- `stg_telemetry` is incremental and `dim_device` is a snapshot: a bad run can leave bad
  rows. **Human**: Delta keeps versions, so
  `DESCRIBE HISTORY lakehouse.dbt_dev.stg_telemetry` and
  `RESTORE TABLE lakehouse.dbt_dev.stg_telemetry TO VERSION AS OF <n>`; for stg a
  `--full-refresh` rebuild from bronze is the simpler path. A snapshot cannot be rebuilt:
  restore it, or its SCD2 history is lost.

## CronJob publisher

`30 * * * *` UTC, image `publisher:0.1.0`, Secrets `databricks-dbt`, `tb-credentials`,
`tb-device-tokens`.

**Means.** Sends `srv_thingsboard` rows with `ts_ms > watermark - 48 h` to ThingsBoard
(devices through the device API, assets through the REST API), then writes
`rows_published` and `gold_lag_min` on the Pipeline asset and, last, the new watermark
(`publisher_watermark_ms`, server attribute of Pipeline). A failed run leaves the watermark
as it was, so the next run sends the same rows again.

**Check.**
```bash
kubectl -n lakehouse-dev logs job/<publisher-...>
```
A good log: `watermark A -> B`, `published N values`, `gold_lag_min X`. Also: the Pipeline
ops dashboard (`rows_published` per run, `gold_lag_min`). Typical failures:
- `FAIL login failed`: `tb-credentials` wrong;
- `FAIL devices.tokens.json: no token for sensor-0x` or `asset 'X' not found`: ThingsBoard
  was re-provisioned; run `make tb-provision`, then recreate `tb-device-tokens`;
- `sensor-0x: telemetry rejected with HTTP 401`: a device token changed (same fix);
- `FAIL cannot reach ThingsBoard`: ThingsBoard down or not on the `kind` network
  (`docker network inspect kind`);
- `Databricks query failed`: warehouse or token, as for dbt-build.

**Fix.** Fix the cause, then start a run:
`kubectl -n lakehouse-dev create job --from=cronjob/publisher pub-manual-1`.
Locally first: `uv run python -m publisher --dry-run` prints every point it would send.
History older than the 48 h lookback (for example after re-provisioning ThingsBoard):
`uv run python -m publisher --from <day> --to <day>` (a backfill; the watermark stays).

**Roll back.**
- Image: previous `publisher` (see [Images](#images-build-load-roll-back)).
- Wrong values with the right timestamps: fix gold, then backfill the range; ThingsBoard
  overwrites the same key and ts.
- Wrong timestamps or keys (**human**): delete them with the ThingsBoard API
  (`DELETE /api/plugins/telemetry/{entityType}/{entityId}/timeseries/delete?keys=..&startTs=..&endTs=..`),
  then backfill.
- Watermark too far ahead (**human**): set `publisher_watermark_ms` on Pipeline (Entities ->
  Assets -> Pipeline -> Attributes -> Server) to an earlier epoch ms; the next run resends
  from there minus 48 h.

## Deployment generator-live

Not a CronJob: one replica, always on, image `generator:0.1.0`, Secret `tb-device-tokens`.

**Means.** One MQTT client per device sends a clean point every 30 s to
`thingsboard-thingsboard-ce-1:1883` (the live `temperature`, `humidity`, `battery`).

**Check.** `kubectl -n lakehouse-dev logs deploy/generator-live --tail=5` shows
`HH:MM:SSZ sent 5/5` every 30 s. `CrashLoopBackOff` with `cannot reach` or `no CONNACK`:
ThingsBoard is down, or `connection rejected`: a token changed.

**Fix.** Start ThingsBoard on the kind network (from `thingsboard/`:
`docker compose -f docker-compose.yml -f docker-compose.kind.yml up -d`), or
re-provision and recreate `tb-device-tokens`, then
`kubectl -n lakehouse-dev rollout restart deployment/generator-live`.

**Roll back.** `kubectl -n lakehouse-dev scale deployment/generator-live --replicas=0` stops
it; previous image as below. Never run two replicas: each device has a fixed MQTT client id.

## Images: build, load, roll back

Images are loaded into the kind node; nothing is pulled (`imagePullPolicy: Never`).

```bash
docker build -f <component>/Dockerfile -t <image>:0.1.0 .
docker save -o "$env:TEMP\img.tar" <image>:0.1.0; kind load image-archive "$env:TEMP\img.tar" --name lakehouse
```

To roll back: build the previous commit in a worktree
(`git worktree add ../rollback <commit>`, build from there with the same tag), load it, and
the next CronJob run uses it; for generator-live also `rollout restart`. Check what the node
has with `docker exec lakehouse-control-plane crictl images`.

---

## Alert: AWS Budget 80% / 100%

Budget `telemetry-lakehouse-monthly` (5 USD, actual cost), email to `alert_email`.

**Means.** Actual AWS spend this month passed 4 USD (80%) or 5 USD (100%). The project's
AWS footprint is the raw bucket, IAM and the state bucket, so this is usually S3 requests
from generator-batch or something outside the project in the same account. Databricks
serverless is billed by Databricks, not here.

**Check.** AWS console -> Billing -> Cost Explorer, group by service and by tag `project`.

**Fix.** Find the service. For S3 from the generator: suspend generator-batch (above) or
widen its schedule. Lifecycle already expires `raw/` after 30 days.

**Roll back.** Nothing to roll back; resume generator-batch when the cause is fixed.

## Alert: dbt source freshness

`make dbt-freshness` (`dbt source freshness`) on `bronze.telemetry_raw._ingested_at`: warn
after 2 h, error after 6 h. `dbt build` does not run it, so run it when charts look stale.

**Means.** Bronze got no new rows for 2 h (warn) or 6 h (error).

**Check.** In order: generator-batch jobs complete? new objects under `raw/telemetry/` for
today? `ingest_bronze` ran (it is paused in dev)?

**Fix.** Run `ingest_bronze` (see above); fix generator-batch if raw/ is empty.

**Roll back.** None needed: ingestion is append-only and catches up.

## Alert: dbt warn tests

Three tests report `WARN` on purpose: the generator injects these defects.

| Test | Means | Worry when |
|---|---|---|
| `no_gap_longer_than` on `stg_telemetry.event_at` (`max_gap_minutes` 120) | a device was silent longer than 2 h | a device other than the configured gap device, or gaps in live hours (a real outage) |
| `accepted_range` on `stg_telemetry.temperature` (-40..85) | outliers 999 / -80 | far more than ~0.5% of readings |
| `assert_dim_device_has_firmware_history` | a device has no second SCD2 version | it stays after the first snapshot run following the drift date |

**Check.** `dbt build` output shows `WARN <n>`; the failing rows:
`uv run dbt test -s <test> --store-failures --target dev` (PowerShell).

**Fix.** Within the expected range: nothing. Outside it: inspect the raw batches for that
device and time.

**Roll back.** Not applicable: warn tests change no data. Never turn a warn into an error
for an injected defect; never lower a real test to warn (rule in `.claude/rules/dbt.md`).

## Alert: CI checks on a pull request

| Job | Means when red | Fix |
|---|---|---|
| lint | ruff, sqlfluff, hadolint, kubeconform or terraform fmt/validate | run the same command locally |
| test | a pytest failed | `uv run pytest` |
| plan | `terraform plan` failed; `AccessDenied` names a missing read action | add the action to `tl-github-plan` (`infra/github.tf`) with `tf-change` |
| review | Claude left comments | read them; it only flags bugs, rule violations and data risks |
| dbt-ci | the PR's models fail on Databricks; the `diagnose` job comments the cause | fix on the branch; transient warehouse errors: re-run the job |

Roll back: do not merge. A bad merge to main: revert the commit; `deploy` rebuilds prod
from the reverted code.

## ThingsBoard alarms

None are configured yet (checked 2026-10-17: 0 alarm rules in the `sensor-th` profile and
no rules on assets), so the Alarms tables on both dashboards stay empty. Two rules fit the
dashboards; add them with a runbook entry like the ones above:
- **Device offline** (profile `sensor-th`): no `temperature` for 10 min (the dbt
  `offline_after_minutes`). Check: generator-live logs; fix: as generator-live.
- **Gold stale** (asset profile `pipeline`): `gold_lag_min` > 90. Check and fix: dbt-build,
  publisher and the freshness check, in that order.
