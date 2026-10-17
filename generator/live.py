"""Live mode: a clean real-time stream to ThingsBoard over MQTT, one point per device / 30 s.

No defects here: ThingsBoard gets only temperature, humidity and battery. Fixing data
defects is the lakehouse's job. Readings use the same functions (and seed) as batch mode.
Device access tokens are the MQTT usernames; they are never printed.
"""

import json
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from generator import GeneratorError
from generator.batch import SLOT_MS
from generator.devices import Device
from generator.readings import event_rng, reading

ROOT = Path(__file__).resolve().parent.parent
TOKENS_FILE = ROOT / "thingsboard" / "devices.tokens.json"
MQTT_HOST = "localhost"
MQTT_PORT = 1883
TOPIC = "v1/devices/me/telemetry"
TIMEOUT_S = 10


def load_tokens(path: Path, devices: list[Device]) -> dict[str, str]:
    """Token per device_id; every device must have one. Errors never show token values."""
    if not path.exists():
        raise GeneratorError(f"{path.name} not found: run make tb-provision first")
    try:
        tokens = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GeneratorError(f"{path.name}: invalid JSON") from exc
    if not isinstance(tokens, dict):
        raise GeneratorError(f"{path.name}: expected a JSON object")
    missing = [
        d.device_id
        for d in devices
        if not isinstance(tokens.get(d.device_id), str) or not tokens[d.device_id]
    ]
    if missing:
        raise GeneratorError(f"{path.name}: no token for {', '.join(missing)}")
    return {d.device_id: tokens[d.device_id] for d in devices}


def build_payload(device: Device, ts_ms: int, seed: int) -> dict:
    clean = reading(device, ts_ms, event_rng(seed, device.device_id, ts_ms))
    return {
        "ts": ts_ms,
        "values": {
            "temperature": clean.temperature,
            "humidity": clean.humidity,
            "battery": clean.battery,
        },
    }


def connect(device_id: str, token: str, host: str, port: int) -> mqtt.Client:
    """Persistent client for one device; paho reconnects on its own after this succeeds."""
    connected = threading.Event()
    result = {}

    def on_connect(client, userdata, flags, reason_code, properties):
        result["reason_code"] = reason_code
        connected.set()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=f"generator-{device_id}"
    )
    client.username_pw_set(token)
    client.on_connect = on_connect
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    try:
        client.connect(host, port, keepalive=30)
    except OSError as exc:
        raise GeneratorError(
            f"cannot reach {host}:{port} ({type(exc).__name__})"
        ) from exc
    client.loop_start()
    if not connected.wait(TIMEOUT_S):
        disconnect(client)
        raise GeneratorError(f"{device_id}: no CONNACK within {TIMEOUT_S}s")
    if result["reason_code"].is_failure:
        disconnect(client)
        raise GeneratorError(
            f"{device_id}: connection rejected: {result['reason_code']}"
        )
    return client


def disconnect(client: mqtt.Client) -> None:
    client.disconnect()
    client.loop_stop()


def publish(client: mqtt.Client, payload: dict) -> bool:
    """QoS 1: True once the broker acknowledged the point."""
    info = client.publish(TOPIC, json.dumps(payload), qos=1)
    try:
        info.wait_for_publish(timeout=TIMEOUT_S)
    except (ValueError, RuntimeError):
        return False
    return info.is_published()


def next_tick(now_ms: int, interval_ms: int) -> int:
    """The first interval boundary after now_ms.

    interval_ms is a multiple of SLOT_MS, so every tick is a 30 s slot and the reading for
    it is the same one batch mode produces for that ts.
    """
    return (now_ms // interval_ms + 1) * interval_ms


def run(
    devices: list[Device],
    tokens: dict[str, str],
    seed: int,
    host: str,
    port: int,
    duration_min: float | None = None,
    interval_ms: int = SLOT_MS,
) -> None:
    """Send on every interval boundary (30 s by default) until Ctrl+C or duration_min."""
    clients: dict[str, mqtt.Client] = {}
    try:
        for device in devices:
            clients[device.device_id] = connect(
                device.device_id, tokens[device.device_id], host, port
            )
        print(f"connected {len(clients)}/{len(devices)} devices to {host}:{port}")

        started = time.time()
        next_ts = next_tick(int(started * 1000), interval_ms)
        while duration_min is None or next_ts / 1000 <= started + duration_min * 60:
            time.sleep(max(0.0, next_ts / 1000 - time.time()))
            sent = sum(
                publish(clients[d.device_id], build_payload(d, next_ts, seed))
                for d in devices
            )
            print(
                f"{time.strftime('%H:%M:%S', time.gmtime(next_ts / 1000))}Z sent {sent}/{len(devices)}"
            )
            next_ts += interval_ms
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    finally:
        for client in clients.values():
            disconnect(client)
