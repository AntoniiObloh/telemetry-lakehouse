"""Bronze ingestion: Auto Loader reads raw telemetry JSON from S3 into a Delta table.

Raw files are kept as they are (duplicates, late events, outliers); cleaning is dbt's job.
Runs as a serverless job task, so the stream uses trigger(availableNow=True): it processes
every new file once and stops. Schema and checkpoint live under artifacts/ in the same bucket.
"""

import argparse
import re

TABLE = "lakehouse.bronze.telemetry_raw"
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def build_paths(bucket: str) -> dict[str, str]:
    """S3 locations for one bucket; rejects values like 's3://name' or 'name/prefix'."""
    if not BUCKET_RE.match(bucket):
        raise ValueError("bucket must be a bare bucket name, without s3:// or a path")
    base = f"s3://{bucket}"
    return {
        "source": f"{base}/raw/telemetry/",
        "schema": f"{base}/artifacts/schemas/telemetry",
        "checkpoint": f"{base}/artifacts/checkpoints/telemetry_raw",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket", required=True, help="raw bucket name, no s3:// prefix"
    )
    paths = build_paths(parser.parse_args().bucket)

    # Imported here so the module can be loaded (and tested) without pyspark installed.
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    query = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.schemaLocation", paths["schema"])
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .load(paths["source"])
        .selectExpr(
            "*",
            "current_timestamp() as _ingested_at",
            "_metadata.file_path as _source_file",
        )
        .writeStream.option("checkpointLocation", paths["checkpoint"])
        # Lets a new source column (firmware) be added to the existing table.
        .option("mergeSchema", "true")
        .trigger(availableNow=True)
        .toTable(TABLE)
    )
    query.awaitTermination()


if __name__ == "__main__":
    main()
