"""Provision ThingsBoard CE entities for the telemetry-lakehouse demo.

Idempotent by name: re-running never creates duplicates.
- Devices from generator/devices.csv (type sensor-th) with server attributes
  site, model, latitude, longitude.
- Assets: Fleet (fleet), Site A / Site B (site), Pipeline (pipeline).
- Contains relations: Fleet -> Site A/B, Site -> its devices.
- Device access tokens -> thingsboard/devices.tokens.json (never printed).

Additive only: nothing is deleted or re-linked. If a device moves to another site in the
CSV, the old Site -> device relation stays and has to be removed by hand.

Credentials (TB_URL, TB_USERNAME, TB_PASSWORD) come from thingsboard/.env;
variables already set in the environment take precedence.
"""

import csv
import json
import os
import sys
from pathlib import Path

import requests
import urllib3
from tb_rest_client.models.models_ce import Asset, EntityRelation
from tb_rest_client.rest import ApiException
from tb_rest_client.rest_client_ce import RestClientCE

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "thingsboard" / ".env"
DEVICES_CSV = ROOT / "generator" / "devices.csv"
TOKENS_FILE = ROOT / "thingsboard" / "devices.tokens.json"

REQUIRED_ENV = ("TB_URL", "TB_USERNAME", "TB_PASSWORD")
DEVICE_TYPE = "sensor-th"
FLEET = ("Fleet", "fleet")
PIPELINE = ("Pipeline", "pipeline")
SITE_TYPE = "site"
SITE_ASSETS = {"site-a": "Site A", "site-b": "Site B"}
EXPECTED_DEVICES = 5


class ProvisionError(Exception):
    """Expected failure with a message that is safe to print."""


def load_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; environment variables override file values."""
    values: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    for key in REQUIRED_ENV:
        if os.environ.get(key):
            values[key] = os.environ[key]
    missing = [key for key in REQUIRED_ENV if not values.get(key)]
    if missing:
        raise ProvisionError(
            f"missing in {path.relative_to(ROOT)} or environment: {', '.join(missing)}"
        )
    return values


def load_devices(path: Path) -> list[dict]:
    """Read and validate the device list before touching the API."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    devices = []
    for line_no, row in enumerate(rows, start=2):
        name = (row.get("device_id") or "").strip()
        site = (row.get("site") or "").strip()
        if not name:
            raise ProvisionError(f"{path.name}:{line_no}: empty device_id")
        if site not in SITE_ASSETS:
            raise ProvisionError(
                f"{path.name}:{line_no}: unknown site '{site}', expected one of {sorted(SITE_ASSETS)}"
            )
        try:
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProvisionError(
                f"{path.name}:{line_no}: bad latitude/longitude"
            ) from exc
        devices.append(
            {
                "name": name,
                "site": site,
                "model": (row.get("model") or "").strip(),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    names = [d["name"] for d in devices]
    if len(set(names)) != len(names):
        raise ProvisionError(f"{path.name}: duplicate device_id values")
    if len(devices) != EXPECTED_DEVICES:
        raise ProvisionError(
            f"{path.name}: expected {EXPECTED_DEVICES} devices, found {len(devices)}"
        )
    return devices


def report(kind: str, name: str, created: bool) -> None:
    print(f"{kind} {name} - {'created' if created else 'exists'}")


def get_or_create_asset(
    client: RestClientCE, name: str, asset_type: str
) -> tuple[Asset, bool]:
    try:
        asset = client.get_tenant_asset(name)
    except ApiException as exc:
        if exc.status != 404:
            raise
        return client.save_asset(Asset(name=name, type=asset_type)), True
    if asset.type != asset_type:
        print(
            f"WARN asset {name} has type '{asset.type}', expected '{asset_type}' (left unchanged)"
        )
    return asset, False


def get_or_create_device(client: RestClientCE, name: str):
    try:
        device = client.get_tenant_device(name)
    except ApiException as exc:
        if exc.status != 404:
            raise
        # The Device model requires device_profile_id; a plain dict lets the server
        # find-or-create the device profile named after the type.
        return client.save_device(body={"name": name, "type": DEVICE_TYPE}), True
    if device.type != DEVICE_TYPE:
        print(
            f"WARN device {name} has type '{device.type}', expected '{DEVICE_TYPE}' (left unchanged)"
        )
    return device, False


def ensure_contains(client: RestClientCE, from_id, to_id) -> None:
    """Saving an existing relation is an upsert, so this is idempotent."""
    client.save_relation(
        EntityRelation(_from=from_id, to=to_id, type="Contains", type_group="COMMON")
    )


def write_tokens(path: Path, tokens: dict[str, str]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(dict(sorted(tokens.items())), indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def provision(client: RestClientCE, devices: list[dict]) -> None:
    fleet, created = get_or_create_asset(client, *FLEET)
    report("asset", FLEET[0], created)
    _, created = get_or_create_asset(client, *PIPELINE)
    report("asset", PIPELINE[0], created)

    sites = {}
    for site_key, site_name in SITE_ASSETS.items():
        site, created = get_or_create_asset(client, site_name, SITE_TYPE)
        report("asset", site_name, created)
        ensure_contains(client, fleet.id, site.id)
        sites[site_key] = site
    print(f"relations {FLEET[0]} -> {', '.join(SITE_ASSETS.values())} - ok")

    tokens: dict[str, str] = {}
    for spec in devices:
        device, created = get_or_create_device(client, spec["name"])
        report("device", spec["name"], created)
        ensure_contains(client, sites[spec["site"]].id, device.id)
        client.save_device_attributes(
            device.id,
            "SERVER_SCOPE",
            {
                "site": spec["site"],
                "model": spec["model"],
                "latitude": spec["latitude"],
                "longitude": spec["longitude"],
            },
        )
        tokens[spec["name"]] = client.get_device_credentials_by_device_id(
            device.id
        ).credentials_id

    write_tokens(TOKENS_FILE, tokens)
    print(f"relations Site -> devices, server attributes - ok ({len(devices)} devices)")
    print(
        f"tokens written to {TOKENS_FILE.relative_to(ROOT).as_posix()} ({len(tokens)} devices)"
    )


def main() -> int:
    try:
        env = load_env(ENV_FILE)
        devices = load_devices(DEVICES_CSV)
        with RestClientCE(base_url=env["TB_URL"]) as client:
            client.login(username=env["TB_USERNAME"], password=env["TB_PASSWORD"])
            # login() does not raise on bad credentials: the token just stays empty.
            if not client.token_info.get("token"):
                raise ProvisionError("login failed: check TB_USERNAME/TB_PASSWORD")
            provision(client, devices)
    except ProvisionError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    except ApiException as exc:
        # str(exc) contains response headers and body; print only status and reason.
        print(f"FAIL ThingsBoard API error: {exc.status} {exc.reason}", file=sys.stderr)
        return 1
    except (requests.RequestException, urllib3.exceptions.HTTPError) as exc:
        print(f"FAIL cannot reach ThingsBoard: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
