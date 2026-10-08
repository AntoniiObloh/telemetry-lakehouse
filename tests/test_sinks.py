"""Key and file format, the file sink, and the S3 sink against a botocore Stubber."""

import json
import re

import boto3
import pytest
from botocore.stub import Stubber

from generator import GeneratorError
from generator.batch import Batch
from generator.sinks import FileSink, S3Sink, batch_key, encode_batch
from tests.conftest import to_ms

KEY_RE = re.compile(r"^raw/telemetry/date=\d{4}-\d{2}-\d{2}/batch_\d{8}T\d{6}Z\.jsonl$")
SENT_AT = to_ms("2026-10-08T12:05")


@pytest.fixture
def batch():
    record = {
        "event_id": "e1",
        "device_id": "sensor-01",
        "ts": SENT_AT - 1000,
        "sent_at": SENT_AT,
    }
    return Batch(sent_at=SENT_AT, records=[record, dict(record, event_id="e2")])


@pytest.fixture
def s3_client():
    return boto3.client(
        "s3",
        region_name="eu-west-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def test_key_matches_sent_at(batch):
    key = batch_key(batch)
    assert KEY_RE.match(key)
    assert key == "raw/telemetry/date=2026-10-08/batch_20261008T120500Z.jsonl"


def test_key_date_follows_utc_midnight():
    key = batch_key(Batch(sent_at=to_ms("2026-10-09T00:00"), records=[]))
    assert "date=2026-10-09/" in key and "batch_20261009T000000Z" in key


def test_encoding_is_compact_lf_json_lines(batch):
    raw = encode_batch(batch)
    assert b"\r" not in raw and raw.endswith(b"\n")
    lines = raw.decode("utf-8").splitlines()
    assert [json.loads(line)["event_id"] for line in lines] == ["e1", "e2"]
    assert ", " not in lines[0] and ": " not in lines[0]


def test_file_sink_writes_atomically_and_overwrites(batch, tmp_path):
    sink = FileSink(tmp_path)
    key = sink.write(batch)
    path = tmp_path / key
    assert path.read_bytes() == encode_batch(batch)
    assert sink.write(batch) == key
    assert path.read_bytes() == encode_batch(batch)
    assert list(tmp_path.rglob("*.tmp")) == []


def test_s3_sink_puts_the_batch(batch, s3_client):
    with Stubber(s3_client) as stubber:
        stubber.add_response(
            "put_object",
            {},
            {
                "Bucket": "raw-bucket",
                "Key": batch_key(batch),
                "Body": encode_batch(batch),
                "ContentType": "application/x-ndjson",
            },
        )
        assert S3Sink("raw-bucket", s3_client).write(batch) == batch_key(batch)
        stubber.assert_no_pending_responses()


def test_s3_sink_reads_bucket_from_env(monkeypatch, s3_client):
    monkeypatch.setenv("RAW_BUCKET", "from-env")
    assert S3Sink(client=s3_client).bucket == "from-env"


def test_s3_sink_needs_a_bucket(monkeypatch, s3_client):
    monkeypatch.delenv("RAW_BUCKET", raising=False)
    with pytest.raises(GeneratorError, match="RAW_BUCKET is not set"):
        S3Sink(client=s3_client)


def test_s3_error_reports_only_the_code(batch, s3_client):
    with Stubber(s3_client) as stubber:
        stubber.add_client_error("put_object", "AccessDenied", "detail text")
        with pytest.raises(GeneratorError) as excinfo:
            S3Sink("raw-bucket", s3_client).write(batch)
    assert str(excinfo.value) == "S3 put_object failed: AccessDenied"
