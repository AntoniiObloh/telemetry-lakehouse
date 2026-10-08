"""The bronze task's pure parts; the Spark stream itself runs only in Databricks."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent / "databricks" / "src" / "ingest_bronze.py"
)


@pytest.fixture(scope="module")
def ingest():
    # Loaded by path: the repo folder "databricks" would clash with the databricks-sdk package.
    spec = importlib.util.spec_from_file_location("ingest_bronze", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(
        module
    )  # works without pyspark: it is imported inside main()
    return module


def test_paths_follow_the_bucket_layout(ingest):
    paths = ingest.build_paths("my-raw-bucket")
    assert paths == {
        "source": "s3://my-raw-bucket/raw/telemetry/",
        "schema": "s3://my-raw-bucket/artifacts/schemas/telemetry",
        "checkpoint": "s3://my-raw-bucket/artifacts/checkpoints/telemetry_raw",
    }


@pytest.mark.parametrize(
    "bad", ["", "s3://my-raw-bucket", "my-raw-bucket/raw", "UPPER", "a"]
)
def test_bucket_must_be_a_bare_name(ingest, bad):
    with pytest.raises(ValueError, match="bare bucket name"):
        ingest.build_paths(bad)


def test_target_table_is_the_bronze_table(ingest):
    assert ingest.TABLE == "lakehouse.bronze.telemetry_raw"
