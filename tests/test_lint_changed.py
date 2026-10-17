"""The PostToolUse linter (.claude/hooks/lint_changed.py): routing, exit codes, env repair."""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = (
    Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "lint_changed.py"
)
_spec = importlib.util.spec_from_file_location("lint_changed", HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)
ROOT = hook.ROOT


@pytest.mark.parametrize(
    ("rel", "linter"),
    [
        ("dbt/models/marts/fct_device_hourly.sql", "lint_sql"),
        ("dbt/snapshots/dim_device.sql", "lint_sql"),
        ("dbt/tests/generic/no_gap_longer_than.sql", "lint_sql"),
        ("dbt/macros/generate_schema_name.sql", None),
        ("dbt/target/compiled/x.sql", None),
        ("generator/batch.py", "lint_python"),
        (".claude/hooks/lint_changed.py", "lint_python"),
        ("dbt/Dockerfile", "lint_dockerfile"),
        ("generator/Dockerfile", "lint_dockerfile"),
        ("dbt/Dockerfile.dockerignore", None),
        ("k8s/base/generator-batch.yaml", "lint_k8s"),
        ("k8s/overlays/kind/kustomization.yaml", "lint_k8s"),
        ("k8s/README.md", None),
        ("thingsboard/docker-compose.yml", None),
        ("infra/s3.tf", None),
    ],
)
def test_linter_for(rel, linter):
    found = hook.linter_for(rel)
    assert (found.__name__ if found else None) == linter


def test_databricks_env_undoes_the_git_bash_rewrite(monkeypatch):
    monkeypatch.setenv(
        "DATABRICKS_HTTP_PATH", "C:/Program Files/Git/sql/1.0/warehouses/abc"
    )
    assert hook.databricks_env()["DATABRICKS_HTTP_PATH"] == "/sql/1.0/warehouses/abc"


def test_databricks_env_keeps_a_correct_path(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc")
    assert hook.databricks_env()["DATABRICKS_HTTP_PATH"] == "/sql/1.0/warehouses/abc"


def completed(returncode: int, out: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, out.encode(), b"")


def run_main(monkeypatch, capsys, event: dict) -> tuple[int, str]:
    payload = io.BytesIO(json.dumps(event).encode("utf-8"))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(payload))
    code = hook.main()
    return code, capsys.readouterr().err


def write_event(rel: str, tool: str = "Edit") -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": str(ROOT / rel)}}


def test_lint_errors_exit_2_with_the_output(monkeypatch, capsys):
    monkeypatch.setattr(hook, "run", lambda *a, **k: completed(1, "batch.py:1:1: F401"))
    code, err = run_main(monkeypatch, capsys, write_event("generator/batch.py"))
    assert code == 2
    assert "Lint errors in generator/batch.py" in err
    assert "F401" in err


def test_clean_file_exits_0(monkeypatch, capsys):
    monkeypatch.setattr(hook, "run", lambda *a, **k: completed(0))
    assert run_main(monkeypatch, capsys, write_event("generator/batch.py")) == (0, "")


def test_hadolint_lines_name_the_file(monkeypatch, capsys):
    monkeypatch.setattr(
        hook, "run", lambda *a, **k: completed(1, "-:7 DL3008 warning: Pin versions")
    )
    code, err = run_main(monkeypatch, capsys, write_event("dbt/Dockerfile"))
    assert code == 2
    assert "dbt/Dockerfile:7 DL3008" in err


def test_docker_down_exits_1_not_2(monkeypatch, capsys):
    monkeypatch.setattr(
        hook,
        "run",
        lambda *a, **k: completed(125, "Cannot connect to the Docker daemon at npipe"),
    )
    code, err = run_main(monkeypatch, capsys, write_event("dbt/Dockerfile"))
    assert code == 1
    assert "not linted" in err


def test_missing_databricks_env_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(
        hook,
        "run",
        lambda *a, **k: completed(
            1, "Env var required but not provided: 'DATABRICKS_HOST'"
        ),
    )
    event = write_event("dbt/models/marts/fct_device_hourly.sql")
    assert run_main(monkeypatch, capsys, event)[0] == 1


def test_expired_databricks_login_exits_1(monkeypatch, capsys):
    message = (
        "TMP | Error received from dbt during project compilation.\n"
        "dbt_common.exceptions.base.DbtDatabaseError: Database Error\n"
        "auth: databricks-cli: cannot get access token: refresh token is invalid"
    )
    monkeypatch.setattr(hook, "run", lambda *a, **k: completed(1, message))
    event = write_event("dbt/models/marts/fct_device_hourly.sql")
    assert run_main(monkeypatch, capsys, event)[0] == 1


def test_missing_tool_exits_1(monkeypatch, capsys):
    def no_uv(*args, **kwargs):
        raise hook.CannotLint("uv not found")

    monkeypatch.setattr(hook, "run", no_uv)
    assert run_main(monkeypatch, capsys, write_event("generator/batch.py"))[0] == 1


def test_k8s_reports_a_broken_kustomization(monkeypatch, capsys):
    monkeypatch.setattr(
        hook, "run", lambda *a, **k: completed(1, "Error: accumulating resources")
    )
    code, err = run_main(monkeypatch, capsys, write_event("k8s/base/dbt-build.yaml"))
    assert code == 2
    assert "kubectl kustomize k8s/" in err


@pytest.mark.parametrize(
    "event",
    [
        {
            "tool_name": "Read",
            "tool_input": {"file_path": str(ROOT / "generator/batch.py")},
        },
        {"tool_name": "Write", "tool_input": {"file_path": "C:/elsewhere/x.py"}},
        write_event("infra/s3.tf", "Write"),
        write_event("generator/does_not_exist.py"),
    ],
)
def test_nothing_to_lint_exits_0(monkeypatch, capsys, event):
    def must_not_run(*args, **kwargs):
        raise AssertionError("no linter should run")

    monkeypatch.setattr(hook, "run", must_not_run)
    assert run_main(monkeypatch, capsys, event) == (0, "")


def test_real_ruff_finds_an_unused_import(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import os\n", encoding="utf-8")
    assert "F401" in hook.lint_python(bad)
