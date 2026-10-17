# k8s

Kustomize manifests for the local kind cluster `lakehouse`, namespace `lakehouse-dev`.

| Resource | Kind | Schedule | Secret |
|---|---|---|---|
| `generator-live` | Deployment (1 replica) | always on | `tb-device-tokens` |
| `generator-batch` | CronJob | `*/5 * * * *` UTC | `generator-aws` |
| `dbt-build` | CronJob | `15 * * * *` UTC | `databricks-dbt` |
| `publisher` | CronJob | `30 * * * *` UTC | `databricks-dbt`, `tb-credentials`, `tb-device-tokens` |

- `base/`: the resources, namespace, labels and image tags.
- `overlays/kind/`: `imagePullPolicy: Never` (images are loaded into the node with
  `kind load image-archive`) and `RAW_BUCKET`, the raw bucket of this environment.

## Before the first apply

1. Cluster: `kind create cluster --name lakehouse`.
2. Images `generator:0.1.0`, `dbt-runner:0.1.0` and `publisher:0.1.0` built and loaded
   into kind.
3. ThingsBoard on the `kind` Docker network: from `thingsboard/`,
   `docker compose -f docker-compose.yml -f docker-compose.kind.yml up -d`, so pods
   reach it as `thingsboard-thingsboard-ce-1`.
4. Secrets, created by hand and never committed. Values are taken from files and
   environment variables, so they are never typed or pasted (PowerShell):

```powershell
# Device tokens written by `make tb-provision`.
kubectl -n lakehouse-dev create secret generic tb-device-tokens `
  --from-file=devices.tokens.json=thingsboard/devices.tokens.json

# Access key of the IAM user tl-generator. RAW_BUCKET and the region are not secrets:
# the overlay and base/generator-batch.yaml set them.
kubectl -n lakehouse-dev create secret generic generator-aws `
  --from-literal=AWS_ACCESS_KEY_ID=$env:AWS_ACCESS_KEY_ID `
  --from-literal=AWS_SECRET_ACCESS_KEY=$env:AWS_SECRET_ACCESS_KEY

# The same variables dbt/profiles.yml reads locally.
kubectl -n lakehouse-dev create secret generic databricks-dbt `
  --from-literal=DATABRICKS_HOST=$env:DATABRICKS_HOST `
  --from-literal=DATABRICKS_HTTP_PATH=$env:DATABRICKS_HTTP_PATH `
  --from-literal=DBT_ENV_SECRET_DATABRICKS_TOKEN=$env:DBT_ENV_SECRET_DATABRICKS_TOKEN

# ThingsBoard tenant administrator for the publisher: only these two lines of
# thingsboard/.env (written without quotes), not POSTGRES_PASSWORD.
$tbEnv = "$env:TEMP\tb-credentials.env"
Select-String -Path thingsboard/.env -Pattern '^(TB_USERNAME|TB_PASSWORD)=' |
  ForEach-Object { $_.Line } | Set-Content -Encoding ascii $tbEnv
kubectl -n lakehouse-dev create secret generic tb-credentials --from-env-file=$tbEnv
Remove-Item $tbEnv
```

Run these from PowerShell. Git Bash rewrites `DATABRICKS_HTTP_PATH` into a Windows path.

## Validate and apply

```powershell
make k8s-validate                       # kustomize + kubeconform in Docker, no cluster needed
kubectl apply -k k8s/overlays/kind      # context must be kind-lakehouse
```

## Notes

- `generator-batch` writes the one batch that closed at the last 5-minute boundary, so
  consecutive runs never write the same batch twice. A run that starts more than 2 minutes
  late is skipped (`startingDeadlineSeconds`), and its batch is missing from raw/.
- `generator-batch` runs with `--gap-device none`: the CLI derives the 3-hour gap inside
  the window, and a 5-minute window cannot hold it. Duplicates, late events, drift and
  outliers stay on.
- `dbt-build` runs 10 minutes after the bronze job (minute 5); dev target only.
- `publisher` runs 15 minutes after `dbt-build`. It re-sends the last
  `PUBLISH_LOOKBACK_HOURS` (default 48) before its watermark, because hourly values change
  while the hour fills up and when late events arrive; ThingsBoard overwrites the same key
  and ts. The watermark is the server attribute `publisher_watermark_ms` of the Pipeline
  asset, written only after everything else succeeded.
