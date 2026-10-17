# telemetry-lakehouse

Five simulated IoT sensors flow through a small but complete lakehouse (S3, Databricks,
dbt) into live ThingsBoard dashboards, built to practise data engineering end to end with
Claude Code.

## Pipeline

```text
generator ──MQTT, every 30 s────────────────────────────────────────────────┐
    │                                                                        ▼
    └─ JSONL batches, every 5 min ─► S3 raw/ ─► Auto Loader ─► bronze ─► dbt ─► gold ─► publisher ─► ThingsBoard
       (duplicates, late events,      (Terraform)  (Databricks job)  (Delta)  silver   srv_thingsboard  (hourly :30)  dashboards
        drift, gap, outliers)                                                 gold     (contract)
```

| Stage | Where | Runs |
|---|---|---|
| Generator: 5 devices, deterministic, 5 injected data defects | `generator/` | k8s CronJob `generator-batch` (*/5) and Deployment `generator-live` |
| Raw bucket, IAM roles, budget, CI role (OIDC) | `infra/` (Terraform) | applied by hand |
| Bronze: Auto Loader into `lakehouse.bronze.telemetry_raw` | `databricks/` (Asset Bundle) | Databricks job `ingest_bronze` (minute 5) |
| Silver and gold: dedup, hourly facts, device health, SCD2 dimension | `dbt/` | k8s CronJob `dbt-build` (minute 15) |
| gold -> ThingsBoard with a watermark, idempotent, backfill | `publisher/` | k8s CronJob `publisher` (minute 30) |
| Dashboards Fleet and Pipeline ops, as code | `thingsboard/` | docker compose (local) or ThingsBoard Cloud |
| Local Kubernetes (kind) | `k8s/` | kustomize base + kind overlay |
| CI: lint, tests, terraform plan, Claude review, dbt slim CI | `.github/workflows/` | every pull request; `deploy` on main |

Why it is built this way: [docs/adr/](docs/adr/). What to do when something breaks:
[docs/runbook.md](docs/runbook.md).

**Public dashboard:** [Fleet on ThingsBoard Cloud](https://thingsboard.cloud/dashboard/7429bf50-ca30-11f1-adf1-29dbfc808686?publicId=31006100-ca32-11f1-afdc-397ada9afcbe)
(read-only, no login).

## Run it in 15 minutes

### Prerequisites

- Windows 10 with PowerShell 5.1 and Git Bash (the commands below are for PowerShell),
  [uv](https://docs.astral.sh/uv/), Docker Desktop, GNU make, Terraform 1.16, the
  Databricks CLI, kind and kubectl.
- An AWS account (credentials in your shell, for example `aws login`).
- A Databricks workspace (Free Edition works) with a SQL warehouse, and these variables
  in your environment: `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`,
  `DBT_ENV_SECRET_DATABRICKS_TOKEN`.
- Files you create yourself, never committed: `thingsboard/.env` (`TB_URL`, `TB_USERNAME`,
  `TB_PASSWORD`, `POSTGRES_PASSWORD`), `infra/backend.hcl` and `infra/terraform.tfvars`
  (copy the `.example` files).

### Steps

```powershell
uv sync                                          # Python env from uv.lock
make doctor                                      # checks tools and variables

# 1. ThingsBoard (http://localhost:8080) with devices, assets and dashboards
cd thingsboard; docker compose up -d; cd ..
make tb-provision                                # devices, Fleet / Site A / Site B / Pipeline, tokens
make tb-import FILE=thingsboard/dashboards/Fleet.json
make tb-import FILE="thingsboard/dashboards/Pipeline ops.json"

# 2. AWS: raw bucket, IAM, budget (review the plan, then apply it yourself)
terraform -chdir=infra init -backend-config=backend.hcl
terraform -chdir=infra plan -out=tf.plan
terraform -chdir=infra apply tf.plan

# 3. Data: a day of batches -> bronze -> dbt -> ThingsBoard
$env:RAW_BUCKET = terraform -chdir=infra output -raw raw_bucket_name
uv run python -m generator batch --sink s3 --start 2026-10-06 --end 2026-10-07
cd databricks; databricks bundle deploy -t dev; databricks bundle run ingest_bronze -t dev --var bucket=$env:RAW_BUCKET; cd ..
make dbt-deps
make dbt-build
uv run python -m publisher --from 2026-10-06 --to 2026-10-06

# 4. Optional: the scheduled jobs in kind
kind create cluster --name lakehouse
make k8s-validate                                # then see k8s/README.md (images, secrets, apply)
```

Checks along the way: `uv run pytest`, `make dbt-lint`, `make dbt-freshness`,
`uv run python -m publisher --dry-run`.

ThingsBoard Cloud instead of the local one: put its `TB_*` into `thingsboard/.env.cloud` and
add `TB_ENV=thingsboard/.env.cloud` to the `tb-*` targets (`--env-file` for the publisher and
the generator's `--tokens-file`).

## How I built this with Claude Code

The project was built in Claude Code sessions: I wrote each step as a short task (often a
pasted spec), Claude planned, implemented, tested and committed, and I applied what touches
the cloud myself.

What the repository shows:

| | |
|---|---|
| Time | first commit 2026-10-06, last so far 2026-10-14 |
| Commits | 20 in this repo, plus 2 in the [lakehouse-toolkit](../lakehouse-toolkit) plugin |
| Tests | 162 pytest tests (plus an end-to-end idempotency test, skipped by default) |
| dbt | 7 resources (seed, staging, intermediate, snapshot, 3 marts), 40 nodes with tests |
| Claude tooling | skills `tf-change`, `new-dbt-model`, `tb-dashboard`, `ship-model`; agents `log-reader`, `dbt-reviewer`, `infra-reviewer`; a PreToolUse guard and a PostToolUse lint hook |

What it cost:

| | Source | Value |
|---|---|---|
| AWS | Cost Explorer, 2026-10-01 to 10-17 (estimated) | 0.00 USD: S3, IAM and Budgets stayed inside the free tier |
| Databricks | Free Edition | 0 USD (daily serverless quotas instead of a bill) |
| ThingsBoard | local CE in Docker, ThingsBoard Cloud free plan | 0 USD |
| Claude | Claude Pro subscription | 20 USD a month, flat; plan limits, no per-token bill |
| Claude tokens, this project | the session transcripts of this repo | 1.25 M output, 8.5 M cache writes, 312 M cache reads |
| The same tokens at API prices | `npx ccusage@latest session`, the 7 sessions of this repo | about 140 USD (133 USD of it one long session) |

The tokens are what the sessions used, not what was paid: on a subscription they count
against the plan's limits, and the API price is only what the same work would cost
pay-as-you-go. Almost all of the input is re-read prompt cache: a long session sends its
context again on every turn, which is also why one session holds most of the cost.

Still to fill in:

| | Source | Value |
|---|---|---|
| Claude tokens on the Pipeline ops dashboard | Claude token telemetry (step 8.5) | _TODO_ |
| Clarifications for the first model vs. the tenth | learning-log | _TODO_ |

What worked:
- **Rules in files, not in prompts.** `CLAUDE.md` and path-scoped `.claude/rules/` (dbt,
  generator) carried the conventions into every session; the review job checks PRs against
  them.
- **Hard limits as hooks.** A PreToolUse guard blocks prod targets, non-kind kubectl,
  `terraform destroy` and reading secret files; a PostToolUse hook lints every file Claude
  writes, so lint errors come back to it immediately.
- **Skills for repeated work.** `new-dbt-model` and `tb-dashboard` turned multi-step
  routines into one request; `ship-model` chains them up to a pull request.
- **Small agents for noisy work.** `log-reader` turns long logs into a ten-line diagnosis
  without filling the main session.

What went wrong, and the fix:
- Git Bash rewrites `DATABRICKS_HTTP_PATH`, so dbt failed with OpenSession 404 only from
  Bash: dbt and sqlfluff run from PowerShell, and the lint hook restores the path.
- A new Databricks external location was blocked for hours (serverless network policy),
  which looked like an IAM problem; the answer was to wait.
- An import overwrote a dashboard made by hand in the UI: dashboard import now works one
  explicit file at a time, after an export.
- A plain watermark would have published every hour once, half full: the publisher
  re-sends a 48-hour lookback, and old ranges go through `--from/--to`.
