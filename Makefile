.PHONY: doctor tb-provision tb-smoke bundle-validate

doctor:
	uv run python scripts/doctor.py .

tb-provision:
	uv run python thingsboard/provision.py

tb-smoke:
	uv run python thingsboard/smoke.py

# Needs BUNDLE_VAR_bucket (the raw bucket name) in the environment.
bundle-validate:
	cd databricks && databricks bundle validate -t dev
