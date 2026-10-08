"""The dbt seed is a copy of the generator's device list; the two must not drift apart."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dbt_seed_matches_generator_devices():
    generator_csv = ROOT / "generator" / "devices.csv"
    seed_csv = ROOT / "dbt" / "seeds" / "devices.csv"
    assert seed_csv.read_bytes() == generator_csv.read_bytes(), (
        "dbt/seeds/devices.csv differs from generator/devices.csv: copy it again"
    )
