.PHONY: doctor tb-provision tb-smoke

doctor:
	uv run python scripts/doctor.py .

tb-provision:
	uv run python thingsboard/provision.py

tb-smoke:
	uv run python thingsboard/smoke.py
