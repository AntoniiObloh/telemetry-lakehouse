"""Sinks: where finished batches go. Both write the same bytes under the same key.

- FileSink: <out_dir>/<key> on local disk, written atomically.
- S3Sink: put_object into the RAW_BUCKET bucket under <key>.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from generator import GeneratorError
from generator.batch import Batch

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "data"
BUCKET_ENV = "RAW_BUCKET"
CONTENT_TYPE = "application/x-ndjson"


class Sink(Protocol):
    def write(self, batch: Batch) -> str:
        """Store the batch and return where it went (a key, safe to print)."""


def batch_key(batch: Batch) -> str:
    """raw/telemetry/date=YYYY-MM-DD/batch_YYYYMMDDTHHMMSSZ.jsonl, both from sent_at (UTC).

    No colons in the file name: Windows does not allow them.
    """
    sent = datetime.fromtimestamp(batch.sent_at / 1000, tz=UTC)
    return f"raw/telemetry/date={sent:%Y-%m-%d}/batch_{sent:%Y%m%dT%H%M%SZ}.jsonl"


def encode_batch(batch: Batch) -> bytes:
    """One compact JSON object per line, LF endings on every OS (same bytes everywhere)."""
    lines = (
        json.dumps(record, separators=(",", ":")) + "\n" for record in batch.records
    )
    return "".join(lines).encode("utf-8")


class FileSink:
    def __init__(self, out_dir: Path = DEFAULT_OUT_DIR):
        self.out_dir = out_dir

    def write(self, batch: Batch) -> str:
        key = batch_key(batch)
        path = self.out_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write next to the target, then swap: a crash never leaves a half-written batch.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(encode_batch(batch))
        os.replace(tmp, path)
        return key


class S3Sink:
    def __init__(self, bucket: str | None = None, client=None):
        self.bucket = bucket or os.environ.get(BUCKET_ENV)
        if not self.bucket:
            raise GeneratorError(f"{BUCKET_ENV} is not set")
        # Credentials come from the default boto3 chain; nothing about them is printed.
        self.client = client or boto3.client("s3")

    def write(self, batch: Batch) -> str:
        key = batch_key(batch)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=encode_batch(batch),
                ContentType=CONTENT_TYPE,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "unknown")
            raise GeneratorError(f"S3 put_object failed: {code}") from exc
        except BotoCoreError as exc:
            raise GeneratorError(f"S3 put_object failed: {type(exc).__name__}") from exc
        return key
