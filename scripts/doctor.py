"""Environment self-check for telemetry-lakehouse (Windows-friendly, stdlib only).

Prints one OK/WARN/FAIL line per check and exits 1 if any check is FAIL.
Never prints secret values: only presence of env vars and success/failure of CLI calls.
"""

import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# tool -> status when missing
TOOLS = {
    "git": FAIL,
    "uv": FAIL,
    "docker": FAIL,
    "aws": WARN,
    "databricks": WARN,
    "terraform": WARN,
    "kind": WARN,
    "kubectl": WARN,
    "jq": WARN,
    "gh": WARN,
}

ENV_VARS = (
    "DATABRICKS_HOST",
    "DATABRICKS_HTTP_PATH",
    "DBT_ENV_SECRET_DATABRICKS_TOKEN",
)

THINGSBOARD_URL = "http://localhost:8080/login"
POSTGRES_CONTAINER = "thingsboard-postgres-1"

Result = tuple[str, str, str]


def run(cmd: list[str], timeout: int = 15) -> tuple[bool, str, str]:
    """Run a command; return (success, stdout, error line).

    Callers must not print stdout of auth commands: it contains account ids and emails.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"timed out after {timeout}s"
    except OSError as exc:
        return False, "", str(exc)
    if proc.returncode == 0:
        return True, proc.stdout.strip(), ""
    lines = [line for line in proc.stderr.strip().splitlines() if line.strip()]
    if not lines:
        return False, "", f"exit code {proc.returncode}"
    # Prefer the actual error line over advisory notices; drop config dumps (host, ids).
    line = next((ln for ln in lines if "error" in ln.lower()), lines[0])
    return False, "", line.split(" Config:")[0]


def check_tool(name: str, missing_status: str) -> Result:
    path = shutil.which(name)
    if path:
        return OK, name, path
    return missing_status, name, "not found in PATH"


def check_cli_call(label: str, binary: str, args: list[str]) -> Result:
    if not shutil.which(binary):
        return FAIL, label, f"{binary} not installed"
    ok, _, err = run([binary, *args])
    return (OK, label, "ok") if ok else (FAIL, label, err)


def check_docker_daemon() -> Result:
    if not shutil.which("docker"):
        return FAIL, "docker daemon", "docker not installed"
    ok, _, err = run(["docker", "info"], timeout=20)
    return (OK, "docker daemon", "running") if ok else (FAIL, "docker daemon", err)


def check_container_health(container: str) -> Result:
    name = f"container {container}"
    if not shutil.which("docker"):
        return WARN, name, "docker not installed"
    fmt = "{{if .State.Health}}{{.State.Health.Status}}{{else}}no healthcheck{{end}}"
    ok, health, err = run(["docker", "inspect", "--format", fmt, container])
    if not ok:
        # Missing container usually means the stack is not up, same as the ThingsBoard check.
        return WARN, name, err
    if health == "healthy":
        return OK, name, health
    if health == "starting":
        return WARN, name, health
    return FAIL, name, health


def check_thingsboard() -> Result:
    name = f"thingsboard {THINGSBOARD_URL}"
    try:
        with urllib.request.urlopen(THINGSBOARD_URL, timeout=5) as resp:
            if resp.status == 200:
                return OK, name, "200"
            return WARN, name, f"status {resp.status}"
    except urllib.error.HTTPError as exc:
        return WARN, name, f"status {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return WARN, name, f"unreachable ({exc})"


def check_env(var: str) -> Result:
    return (OK, var, "set") if os.environ.get(var) else (FAIL, var, "missing")


def collect() -> list[Result]:
    results = [check_tool(name, status) for name, status in TOOLS.items()]
    results.append(check_docker_daemon())
    results.append(check_container_health(POSTGRES_CONTAINER))
    results.append(check_thingsboard())
    results.append(check_cli_call("aws identity", "aws", ["sts", "get-caller-identity"]))
    results.append(check_cli_call("databricks auth", "databricks", ["current-user", "me"]))
    results.extend(check_env(var) for var in ENV_VARS)
    return results


def main() -> int:
    # An optional positional arg (project root, e.g. ".") is accepted and ignored.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    results = collect()
    for status, name, detail in results:
        print(f"{status:<4} {name} - {detail}")
    failed = sum(1 for status, _, _ in results if status == FAIL)
    warned = sum(1 for status, _, _ in results if status == WARN)
    print(f"\n{len(results)} checks: {failed} FAIL, {warned} WARN")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
