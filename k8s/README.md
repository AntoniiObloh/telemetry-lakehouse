# k8s

Kustomize manifests for the local kind cluster `lakehouse`, namespace `lakehouse-dev`.

| Resource | Kind | Schedule | Secret |
|---|---|---|---|
| `generator-live` | Deployment (1 replica) | always on | `tb-device-tokens` |
| `generator-batch` | CronJob | `*/5 * * * *` UTC | `generator-aws` |
| `dbt-build` | CronJob | `15 * * * *` UTC | `databricks-dbt` |

- `base/`: the resources, namespace, labels and image tags.
- `overlays/kind/`: `imagePullPolicy: Never` (images are loaded into the node with
  `kind load image-archive`) and `RAW_BUCKET`, the raw bucket of this environment.

## Before the first apply

1. Cluster: `kind create cluster --name lakehouse`.
2. Images `generator:0.1.0` and `dbt-runner:0.1.0` built and loaded into kind.
3. ThingsBoard on the `kind` Docker network (`thingsboard/docker-compose.yml`), so pods
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
