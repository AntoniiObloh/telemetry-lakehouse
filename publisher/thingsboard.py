"""ThingsBoard: the device HTTP API (requests) and the admin REST API (tb-rest-client).

A device token is part of the device API URL, so URLs and request exceptions are never
printed: errors carry the device name and the HTTP status only.
"""

import json
from collections.abc import Iterable
from pathlib import Path

import requests
from tb_rest_client.rest import ApiException

from publisher import PublisherError
from publisher.payloads import Points

TIMEOUT_S = 30
MAX_POINTS_PER_REQUEST = 500
WATERMARK_KEY = "publisher_watermark_ms"


def chunks(items: Points, size: int = MAX_POINTS_PER_REQUEST) -> Iterable[Points]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def load_tokens(path: Path, devices: Iterable[str]) -> dict[str, str]:
    """Token per device; every device must have one. Errors never show token values."""
    if not path.exists():
        raise PublisherError(f"{path.name} not found: run make tb-provision")
    try:
        tokens = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise PublisherError(f"{path.name}: invalid JSON") from None
    if not isinstance(tokens, dict):
        raise PublisherError(f"{path.name}: expected a JSON object")
    devices = sorted(devices)
    missing = [
        d for d in devices if not isinstance(tokens.get(d), str) or not tokens[d]
    ]
    if missing:
        raise PublisherError(f"{path.name}: no token for {', '.join(missing)}")
    return {device: tokens[device] for device in devices}


class DeviceApi:
    """POST {TB_URL}/api/v1/{token}/telemetry with an array of points."""

    def __init__(self, base_url: str, session: requests.Session | None = None):
        self.base_url = base_url
        self.session = session or requests.Session()

    def send(self, device: str, token: str, device_points: Points) -> None:
        url = f"{self.base_url}/api/v1/{token}/telemetry"
        for chunk in chunks(device_points):
            try:
                response = self.session.post(url, json=chunk, timeout=TIMEOUT_S)
            except requests.RequestException as exc:
                raise PublisherError(
                    f"{device}: cannot reach ThingsBoard ({type(exc).__name__})"
                ) from None
            if response.status_code != 200:
                raise PublisherError(
                    f"{device}: telemetry rejected with HTTP {response.status_code}"
                )


class Admin:
    """Assets, the watermark attribute and asset telemetry through a logged-in client."""

    def __init__(self, client):
        self.client = client
        self.asset_ids: dict[str, object] = {}

    def asset_id(self, name: str):
        if name not in self.asset_ids:
            try:
                self.asset_ids[name] = self.client.get_tenant_asset(name).id
            except ApiException as exc:
                if exc.status == 404:
                    raise PublisherError(
                        f"asset '{name}' not found: run make tb-provision"
                    ) from None
                raise
        return self.asset_ids[name]

    def read_watermark(self, asset_id) -> int:
        """Server attribute publisher_watermark_ms; 0 before the first run."""
        attributes = self.client.get_attributes_by_scope(
            asset_id, "SERVER_SCOPE", WATERMARK_KEY
        )
        for attribute in attributes or []:
            if attribute.get("key") == WATERMARK_KEY:
                return int(attribute["value"])
        return 0

    def write_watermark(self, asset_id, watermark_ms: int) -> None:
        self.client.save_entity_attributes_v2(
            asset_id, "SERVER_SCOPE", {WATERMARK_KEY: watermark_ms}
        )

    def save_telemetry(self, asset_id, asset_points: Points) -> None:
        for chunk in chunks(asset_points):
            self.client.save_entity_telemetry(asset_id, "ANY", chunk)
