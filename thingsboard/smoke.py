"""Smoke test: push two telemetry points per device to ThingsBoard over MQTT.

For every device in thingsboard/devices.tokens.json (written by provision.py):
- a current point at now (epoch ms) with temperature, humidity and battery;
- a historical point at a fixed ts (2026-10-01T00:00:00Z) with temperature only.

The device access token is the MQTT username; tokens are never printed.
Exit code 1 if any device fails.
"""

import json
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parent.parent
TOKENS_FILE = ROOT / "thingsboard" / "devices.tokens.json"

MQTT_HOST = "localhost"
MQTT_PORT = 1883
TOPIC = "v1/devices/me/telemetry"
TIMEOUT_S = 10
HISTORICAL_TS = 1790812800000  # 2026-10-01T00:00:00Z


class SmokeError(Exception):
    """Expected failure with a message that is safe to print (never contains a token)."""


def load_tokens(path: Path) -> dict[str, str]:
    rel = path.relative_to(ROOT).as_posix()
    if not path.exists():
        raise SmokeError(f"{rel} not found: run make tb-provision first")
    try:
        tokens = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SmokeError(f"{rel}: invalid JSON") from exc
    if not isinstance(tokens, dict) or not tokens:
        raise SmokeError(f"{rel}: expected a non-empty JSON object")
    empty = sorted(
        name
        for name, token in tokens.items()
        if not isinstance(token, str) or not token
    )
    if empty:
        raise SmokeError(f"{rel}: empty token for {', '.join(empty)}")
    return tokens


def build_points(now_ms: int) -> list[dict]:
    return [
        {"ts": now_ms, "values": {"temperature": 21.5, "humidity": 45, "battery": 99}},
        {"ts": HISTORICAL_TS, "values": {"temperature": 20.0}},
    ]


def send_points(name: str, token: str, points: list[dict]) -> None:
    """Connect as the device, publish each point with QoS 1 and disconnect."""
    connected = threading.Event()
    result = {}

    def on_connect(client, userdata, flags, reason_code, properties):
        result["reason_code"] = reason_code
        connected.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"smoke-{name}")
    client.username_pw_set(token)
    client.on_connect = on_connect
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    except OSError as exc:
        raise SmokeError(
            f"cannot reach {MQTT_HOST}:{MQTT_PORT} ({type(exc).__name__})"
        ) from exc
    client.loop_start()
    try:
        if not connected.wait(TIMEOUT_S):
            raise SmokeError(f"no CONNACK within {TIMEOUT_S}s")
        reason_code = result["reason_code"]
        # ThingsBoard answers an unknown token with "Not authorized".
        if reason_code.is_failure:
            raise SmokeError(f"connection rejected: {reason_code}")
        for point in points:
            info = client.publish(TOPIC, json.dumps(point), qos=1)
            try:
                info.wait_for_publish(timeout=TIMEOUT_S)
            except (ValueError, RuntimeError) as exc:
                raise SmokeError(str(exc)) from exc
            if not info.is_published():
                raise SmokeError(f"PUBACK not received within {TIMEOUT_S}s")
    finally:
        client.disconnect()
        client.loop_stop()


def main() -> int:
    try:
        tokens = load_tokens(TOKENS_FILE)
    except SmokeError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    points = build_points(int(time.time() * 1000))
    failed = 0
    for name in sorted(tokens):
        try:
            send_points(name, tokens[name], points)
        except SmokeError as exc:
            failed += 1
            print(f"FAIL {name} - {exc}")
            continue
        print(f"OK   {name} - {len(points)} points sent")
    print(f"\n{len(tokens)} devices: {len(tokens) - failed} OK, {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
