"""PostToolUse linter for the file a Write or Edit just changed.

Reads the hook event (JSON) from stdin, takes tool_input.file_path and runs the linter for
that kind of file:
- *.sql under dbt/models, dbt/snapshots, dbt/tests: sqlfluff (dbt templater, needs the
  Databricks env vars; the same folders as make dbt-lint);
- *.py: ruff check;
- Dockerfile: hadolint in Docker;
- k8s/**.yaml: every kustomization under k8s/ is built and checked with kubeconform in
  Docker (a kustomization.yaml on its own is not a Kubernetes object).

Exit codes: 0 clean or nothing to lint; 2 lint errors on stderr, which Claude Code shows
to the model so it fixes them; 1 the linter itself could not run (Docker down, no network,
missing env vars), shown to the user only. Standard library only.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = {"Write", "Edit"}
LINT_ERRORS = 2
CANNOT_LINT = 1

SQLFLUFF_DIRS = ("dbt/models/", "dbt/snapshots/", "dbt/tests/")
HADOLINT_IMAGE = "hadolint/hadolint:v2.14.0"
# Same image and schema version as make k8s-validate.
KUBECONFORM_IMAGE = "ghcr.io/yannh/kubeconform:v0.8.0"
KUBERNETES_VERSION = "1.37.0"
TIMEOUT_S = 150
MAX_OUTPUT_CHARS = 6000

# Output that means the linter could not do its job, not that the file is wrong.
DOCKER_DOWN = ("Cannot connect to the Docker daemon", "error during connect")
NO_DATABRICKS = (
    "Env var required but not provided",
    "OpenSession",
    "Could not connect",
    "Failed to connect",
    "cannot get access token",  # expired databricks-cli OAuth login
    "DbtDatabaseError",
)
NO_SCHEMAS = ("failed downloading schema", "no such host", "dial tcp")


class CannotLint(Exception):
    """The linter could not run; editing the file would not help."""


def run(
    command: list[str], stdin: bytes | None = None, env: dict | None = None
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            input=stdin,
            capture_output=True,
            timeout=TIMEOUT_S,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CannotLint(f"{command[0]} not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise CannotLint(f"{command[0]} timed out after {TIMEOUT_S} s") from exc


def output(result: subprocess.CompletedProcess) -> str:
    return (result.stdout + result.stderr).decode("utf-8", "replace").strip()


def raise_if(text: str, markers: tuple[str, ...], what: str) -> None:
    if any(marker in text for marker in markers):
        raise CannotLint(f"{what}: {text.splitlines()[-1] if text else 'no output'}")


def databricks_env() -> dict:
    """The environment for dbt, with DATABRICKS_HTTP_PATH as it was set.

    Git Bash runs hooks on Windows and rewrites /sql/... into C:/Program Files/Git/sql/...
    for programs it starts; dbt then fails with OpenSession 404.
    """
    env = dict(os.environ)
    http_path = env.get("DATABRICKS_HTTP_PATH", "")
    if not http_path.startswith("/") and "/sql/" in http_path:
        env["DATABRICKS_HTTP_PATH"] = http_path[http_path.index("/sql/") :]
    return env


def lint_sql(path: Path) -> str | None:
    rel = path.relative_to(ROOT).as_posix()
    result = run(["uv", "run", "sqlfluff", "lint", rel], env=databricks_env())
    if result.returncode == 0:
        return None
    text = output(result)
    raise_if(text, NO_DATABRICKS, "sqlfluff could not compile dbt")
    lines = text.splitlines()
    return "\n".join(
        line for line in lines if not line.startswith("=== [dbt templater]")
    )


def lint_python(path: Path) -> str | None:
    result = run(["uv", "run", "ruff", "check", str(path)])
    return output(result) if result.returncode else None


def lint_dockerfile(path: Path) -> str | None:
    result = run(
        ["docker", "run", "--rm", "-i", HADOLINT_IMAGE], stdin=path.read_bytes()
    )
    text = output(result)
    raise_if(text, DOCKER_DOWN, "docker")
    if result.returncode == 0:
        return None
    # hadolint reads stdin, so its lines start with "-:<line>".
    rel = path.relative_to(ROOT).as_posix()
    return "\n".join(
        rel + line[1:] if line.startswith("-:") else line for line in text.splitlines()
    )


def lint_k8s(path: Path) -> str | None:
    problems = []
    for root in sorted(p.parent for p in (ROOT / "k8s").rglob("kustomization.yaml")):
        rel = root.relative_to(ROOT).as_posix()
        built = run(["kubectl", "kustomize", rel])
        if built.returncode:
            problems.append(f"kubectl kustomize {rel}:\n{output(built)}")
            continue
        result = run(
            [
                "docker", "run", "--rm", "-i", KUBECONFORM_IMAGE,
                "-strict", "-summary", "-kubernetes-version", KUBERNETES_VERSION, "-",
            ],
            stdin=built.stdout,
        )  # fmt: skip
        text = output(result)
        raise_if(text, DOCKER_DOWN, "docker")
        raise_if(text, NO_SCHEMAS, "kubeconform could not download schemas")
        if result.returncode:
            problems.append(f"kubeconform {rel}:\n{text}")
    return "\n\n".join(problems) or None


def is_dockerfile(name: str) -> bool:
    if name.endswith(".dockerignore"):
        return False
    return name == "Dockerfile" or name.startswith("Dockerfile.")


def linter_for(rel: str) -> Callable[[Path], str | None] | None:
    """The linter for a repo-relative path (forward slashes), or None."""
    if rel.endswith(".sql"):
        return lint_sql if rel.startswith(SQLFLUFF_DIRS) else None
    if rel.endswith(".py"):
        return lint_python
    if is_dockerfile(rel.rsplit("/", 1)[-1]):
        return lint_dockerfile
    if rel.startswith("k8s/") and rel.endswith((".yaml", ".yml")):
        return lint_k8s
    return None


def main() -> int:
    # utf-8-sig: PowerShell 5.1 pipes text into programs with a BOM.
    event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    file_path = (event.get("tool_input") or {}).get("file_path")
    if event.get("tool_name") not in TOOLS or not isinstance(file_path, str):
        return 0
    path = Path(file_path).resolve()
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:  # outside the repo
        return 0
    lint = linter_for(rel)
    if lint is None or not path.is_file():
        return 0
    try:
        problems = lint(path)
    except CannotLint as exc:
        print(f"lint_changed.py: {rel} not linted ({exc})", file=sys.stderr)
        return CANNOT_LINT
    if problems:
        print(
            f"Lint errors in {rel}, fix them:\n{problems[:MAX_OUTPUT_CHARS]}",
            file=sys.stderr,
        )
        return LINT_ERRORS
    return 0


if __name__ == "__main__":
    sys.exit(main())
