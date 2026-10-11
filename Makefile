.PHONY: doctor tb-provision tb-smoke bundle-validate dbt-deps dbt-build dbt-lint dbt-freshness k8s-validate

doctor:
	uv run python scripts/doctor.py .

tb-provision:
	uv run python thingsboard/provision.py

tb-smoke:
	uv run python thingsboard/smoke.py

# Needs BUNDLE_VAR_bucket (the raw bucket name) in the environment.
bundle-validate:
	cd databricks && databricks bundle validate -t dev

# dbt runs locally only against the dev target; prod runs from CI.
dbt-deps:
	uv run dbt deps --project-dir dbt --profiles-dir dbt

dbt-build:
	uv run dbt build --project-dir dbt --profiles-dir dbt --target dev

# The dbt templater compiles models, so this needs the same env vars as dbt-build.
dbt-lint:
	uv run sqlfluff lint dbt/models dbt/snapshots dbt/tests

# dbt build does not check source freshness; this does (warn 2 h, error 6 h).
dbt-freshness:
	uv run dbt source freshness --project-dir dbt --profiles-dir dbt --target dev

# Renders the kind overlay and checks it against the schemas of the kind node's version.
k8s-validate:
	kubectl kustomize k8s/overlays/kind | docker run --rm -i ghcr.io/yannh/kubeconform:v0.8.0 -strict -summary -kubernetes-version 1.37.0 -
