"""CLI: argument handling, exit codes and the file sink end to end."""

import pytest

from generator.__main__ import main

HOUR = ["--start", "2026-10-05", "--end", "2026-10-05T01:00"]


def test_batch_writes_files_and_reports(tmp_path, capsys):
    code = main(
        [
            "batch",
            "--sink",
            "file",
            *HOUR,
            "--gap-device",
            "none",
            "--out-dir",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "batches  12" in out and "events" in out
    assert len(list(tmp_path.rglob("*.jsonl"))) == 12


def test_no_defects_flag(tmp_path, capsys):
    main(["batch", *HOUR, "--no-defects", "--out-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "duplicates 0, late 0, outliers 0" in out and "gap" not in out


def test_expected_errors_exit_1_without_a_traceback(tmp_path, capsys):
    args = ["batch", "--start", "2026-10-05T00:01", "--end", "2026-10-06"]
    code = main([*args, "--out-dir", str(tmp_path)])
    assert code == 1
    assert "FAIL" in capsys.readouterr().err
    assert not list(tmp_path.rglob("*.jsonl"))


def test_s3_without_bucket_fails_fast(monkeypatch, capsys):
    monkeypatch.delenv("RAW_BUCKET", raising=False)
    code = main(
        ["batch", "--sink", "s3", "--start", "2026-10-05", "--end", "2026-10-06"]
    )
    assert code == 1
    assert "RAW_BUCKET is not set" in capsys.readouterr().err


def test_utc_only_and_required_args():
    with pytest.raises(SystemExit):
        main(["batch", "--start", "2026-10-05T00:00+02:00", "--end", "2026-10-06"])
    with pytest.raises(SystemExit):
        main(["batch", "--end", "2026-10-06"])
