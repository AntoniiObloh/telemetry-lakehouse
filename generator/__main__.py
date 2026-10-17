"""CLI: python -m generator batch|live (see docs/design/generator.md)."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from generator import GeneratorError, live
from generator.batch import DefectConfig, simulate
from generator.devices import load_devices
from generator.sinks import DEFAULT_OUT_DIR, FileSink, S3Sink


def parse_utc(text: str) -> int:
    """A date or date-time in UTC -> epoch ms."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date or date-time: '{text}'") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    elif moment.utcoffset().total_seconds() != 0:
        raise argparse.ArgumentTypeError(f"'{text}': UTC only")
    return int(moment.timestamp() * 1000)


def parse_interval(text: str) -> int:
    """Seconds between live points: a positive multiple of 30 (the reading slot)."""
    try:
        seconds = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not a whole number of seconds: '{text}'"
        ) from None
    if seconds <= 0 or seconds % 30:
        raise argparse.ArgumentTypeError(
            f"{seconds}: must be a positive multiple of 30"
        )
    return seconds


def parse_drift(text: str) -> int | None:
    return None if text.lower() == "none" else parse_utc(text)


def fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%MZ")


def build_parser() -> argparse.ArgumentParser:
    defaults = DefectConfig()
    parser = argparse.ArgumentParser(prog="generator", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    batch = commands.add_parser(
        "batch", help="write 5-minute JSONL batches with defects"
    )
    batch.add_argument("--sink", choices=("file", "s3"), default="file")
    batch.add_argument(
        "--start", type=parse_utc, required=True, help="UTC, multiple of 5 min"
    )
    batch.add_argument(
        "--end", type=parse_utc, required=True, help="UTC, multiple of 5 min"
    )
    batch.add_argument("--seed", type=int, default=defaults.seed)
    batch.add_argument("--dup-rate", type=float, default=defaults.dup_rate)
    batch.add_argument("--late-rate", type=float, default=defaults.late_rate)
    batch.add_argument("--outlier-rate", type=float, default=defaults.outlier_rate)
    batch.add_argument(
        "--drift-from", type=parse_drift, default="2026-10-08", help="date, or 'none'"
    )
    batch.add_argument(
        "--gap-device", default=defaults.gap_device, help="device_id, or 'none'"
    )
    batch.add_argument("--gap-hours", type=float, default=defaults.gap_hours)
    batch.add_argument(
        "--gap-start",
        type=parse_utc,
        help="UTC; default: derived from the seed in the window",
    )
    batch.add_argument(
        "--no-defects", action="store_true", help="clean 30 s cadence only"
    )
    batch.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="file sink root"
    )

    stream = commands.add_parser(
        "live", help="clean real-time stream to ThingsBoard over MQTT"
    )
    stream.add_argument("--seed", type=int, default=defaults.seed)
    stream.add_argument("--host", default=live.MQTT_HOST)
    stream.add_argument("--port", type=int, default=live.MQTT_PORT)
    stream.add_argument(
        "--duration-min", type=float, help="stop after this many minutes"
    )
    stream.add_argument(
        "--interval",
        type=parse_interval,
        default=30,
        help="seconds between points, a multiple of 30 (120 for ThingsBoard Cloud)",
    )
    stream.add_argument(
        "--tokens-file",
        type=Path,
        default=live.TOKENS_FILE,
        help="device tokens (thingsboard/devices.cloud.tokens.json for Cloud)",
    )
    return parser


def run_batch(args: argparse.Namespace) -> int:
    if args.no_defects:
        config = DefectConfig.none(seed=args.seed)
    else:
        gap_device = None if args.gap_device.lower() == "none" else args.gap_device
        config = DefectConfig(
            seed=args.seed,
            dup_rate=args.dup_rate,
            late_rate=args.late_rate,
            outlier_rate=args.outlier_rate,
            drift_from_ms=args.drift_from,
            gap_device=gap_device,
            gap_hours=args.gap_hours,
            gap_start_ms=args.gap_start,
        )
    devices = load_devices()
    sink = FileSink(args.out_dir) if args.sink == "file" else S3Sink()
    result = simulate(devices, args.start, args.end, config)
    for batch in result.batches:
        sink.write(batch)

    target = args.out_dir if args.sink == "file" else f"s3://{sink.bucket}"
    print(f"window   {fmt(args.start)} .. {fmt(args.end)}  seed {config.seed}")
    if result.gap:
        start, end = result.gap
        note = (
            "" if args.start <= start and end <= args.end else "  (outside the window)"
        )
        print(f"gap      {config.gap_device} {fmt(start)} .. {fmt(end)}{note}")
    print(f"batches  {len(result.batches)} -> {target}")
    print(
        f"events   {result.events} (duplicates {result.duplicates}, "
        f"late {result.late}, outliers {result.outliers})"
    )
    return 0


def run_live(args: argparse.Namespace) -> int:
    devices = load_devices()
    tokens = live.load_tokens(args.tokens_file, devices)
    live.run(
        devices,
        tokens,
        args.seed,
        args.host,
        args.port,
        args.duration_min,
        interval_ms=args.interval * 1000,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "batch":
            return run_batch(args)
        return run_live(args)
    except GeneratorError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
