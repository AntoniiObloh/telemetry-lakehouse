"""Export ThingsBoard dashboards to thingsboard/dashboards/ and import them back.

  uv run python thingsboard/dashboards.py export          # every tenant dashboard -> <title>.json
  uv run python thingsboard/dashboards.py import <file>   # update the dashboard with that title

Import updates the dashboard whose title matches the file's "title" (it keeps its id and
links), or creates it when there is none. Credentials (TB_URL, TB_USERNAME, TB_PASSWORD)
come from thingsboard/.env, or from --env-file (thingsboard/.env.cloud for ThingsBoard
Cloud), like provision.py; the script reads them, they are never printed.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import requests
import urllib3
from provision import ROOT, ProvisionError, api_error, env_file_arg, load_env, shown
from tb_rest_client.models.models_ce import Dashboard
from tb_rest_client.rest import ApiException
from tb_rest_client.rest_client_ce import RestClientCE

DASHBOARDS_DIR = Path(__file__).resolve().parent / "dashboards"
PAGE_SIZE = 100
# Characters Windows does not allow in file names.
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def file_name(title: str) -> str:
    """<title>.json, with characters that are not allowed in a file name replaced by _."""
    return UNSAFE.sub("_", title).strip(" .") + ".json"


def tenant_dashboards(client: RestClientCE) -> list:
    infos, page = [], 0
    while True:
        result = client.get_tenant_dashboards(page_size=PAGE_SIZE, page=page)
        infos.extend(result.data)
        if not result.has_next:
            return infos
        page += 1


def find_by_title(client: RestClientCE, title: str) -> Dashboard | None:
    matches = [
        info
        for info in client.get_tenant_dashboards(
            page_size=PAGE_SIZE, page=0, text_search=title
        ).data
        if info.title == title
    ]
    if len(matches) > 1:
        raise ProvisionError(f"{len(matches)} dashboards are titled '{title}'")
    return client.get_dashboard_by_id(matches[0].id) if matches else None


def export_all(client: RestClientCE, out_dir: Path = DASHBOARDS_DIR) -> None:
    infos = tenant_dashboards(client)
    names = [file_name(info.title) for info in infos]
    clashes = sorted({name for name in names if names.count(name) > 1})
    if clashes:
        raise ProvisionError(f"several dashboards map to {', '.join(clashes)}")
    out_dir.mkdir(exist_ok=True)
    for info, name in zip(infos, names, strict=True):
        dashboard = client.get_dashboard_by_id(info.id)
        body = {"title": dashboard.title, "configuration": dashboard.configuration}
        (out_dir / name).write_text(
            json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"dashboard {dashboard.title} - exported to {name}")
    print(f"{len(infos)} dashboards in {out_dir.relative_to(ROOT).as_posix()}/")


def import_file(client: RestClientCE, path: Path) -> None:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProvisionError(f"{path} not found") from None
    except json.JSONDecodeError:
        raise ProvisionError(f"{path.name}: invalid JSON") from None
    if (
        not isinstance(spec, dict)
        or not spec.get("title")
        or "configuration" not in spec
    ):
        raise ProvisionError(
            f"{path.name}: expected an object with title and configuration"
        )
    existing = find_by_title(client, spec["title"])
    dashboard = Dashboard(title=spec["title"], configuration=spec["configuration"])
    if existing is not None:
        dashboard.id = existing.id
        dashboard.version = existing.version
    saved = client.save_dashboard(body=dashboard)
    action = "updated" if existing is not None else "created"
    print(f"dashboard {saved.title} - {action} from {path.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    export_cmd = commands.add_parser(
        "export", help="every tenant dashboard to dashboards/<title>.json"
    )
    env_file_arg(export_cmd)
    import_cmd = commands.add_parser(
        "import", help="update the dashboard titled as FILE"
    )
    import_cmd.add_argument("file", type=Path)
    env_file_arg(import_cmd)
    args = parser.parse_args(argv)
    try:
        if not args.env_file.exists():
            raise ProvisionError(f"{shown(args.env_file)} not found")
        env = load_env(args.env_file)
        with RestClientCE(base_url=env["TB_URL"]) as client:
            client.login(username=env["TB_USERNAME"], password=env["TB_PASSWORD"])
            # login() does not raise on bad credentials: the token just stays empty.
            if not client.token_info.get("token"):
                raise ProvisionError("login failed: check TB_USERNAME/TB_PASSWORD")
            if args.command == "export":
                export_all(client)
            else:
                import_file(client, args.file)
    except ProvisionError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    except ApiException as exc:
        # str(exc) contains response headers and body; print only status and reason.
        print(f"FAIL ThingsBoard API error: {api_error(exc)}", file=sys.stderr)
        return 1
    except (requests.RequestException, urllib3.exceptions.HTTPError) as exc:
        print(f"FAIL cannot reach ThingsBoard: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
