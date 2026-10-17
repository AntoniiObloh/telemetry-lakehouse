"""CLI: python -m publisher [--dry-run] [--from DATE --to DATE].

Without --from/--to: a normal run from the watermark. With them: a backfill of every row
with ts in [--from 00:00, --to + 1 day) UTC, both days included; the watermark is left as
it is. Settings come from the environment (config.py).
"""

import argparse
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
import urllib3
from tb_rest_client.rest import ApiException
from tb_rest_client.rest_client_ce import RestClientCE

from publisher import PublisherError
from publisher.config import ENV_FILE, load_settings
from publisher.gold import DatabricksGold
from publisher.run import Summary, Window, publish
from publisher.thingsboard import Admin, DeviceApi


def fmt(ms: int) -> str:
    if not ms:
        return "none"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%MZ")


def day_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): '{text}'") from None


def backfill_window(first: date | None, last: date | None) -> Window | None:
    """[first 00:00, last + 1 day) UTC, or None for a normal run."""
    if first is None and last is None:
        return None
    if first is None or last is None:
        raise PublisherError("--from and --to go together")
    if last < first:
        raise PublisherError("--to is before --from")
    return Window(day_ms(first), day_ms(last + timedelta(days=1)))


def plan_lines(summary: Summary) -> list[str]:
    """Every point the run sends: entity, then one line per ts with its key=value pairs."""
    lines = []
    for entity_type, entities in (
        ("DEVICE", summary.plan.devices),
        ("ASSET", summary.plan.assets),
    ):
        for name, entity_points in entities.items():
            lines.append(f"{entity_type} {name}")
            for point in entity_points:
                values = "  ".join(f"{k}={v}" for k, v in point["values"].items())
                lines.append(f"  {fmt(point['ts'])}  {values}")
    return lines


def report(summary: Summary) -> None:
    if summary.dry_run:
        print("\n".join(plan_lines(summary)))
    if summary.window:
        print(
            f"backfill  {fmt(summary.window.start_ms)} .. {fmt(summary.window.end_ms)}"
            "  (watermark untouched)"
        )
    else:
        print(
            f"watermark  {fmt(summary.watermark_ms)} -> {fmt(summary.new_watermark_ms)}"
            f"  (rows with ts after {fmt(summary.since_ms)})"
        )
    verb = "would publish" if summary.dry_run else "published"
    print(
        f"{verb}  {summary.rows} values: {summary.devices} devices, "
        f"{summary.assets} assets"
    )
    if not summary.window:
        print(f"gold_lag_min  {summary.gold_lag_min}")
    if summary.dry_run:
        print("dry run: nothing written")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="publisher", description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read and plan, print every point, write nothing",
    )
    parser.add_argument(
        "--from",
        dest="first",
        type=parse_day,
        metavar="YYYY-MM-DD",
        help="backfill: first day, UTC",
    )
    parser.add_argument(
        "--to",
        dest="last",
        type=parse_day,
        metavar="YYYY-MM-DD",
        help="backfill: last day, included",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ENV_FILE,
        help="TB_* of the ThingsBoard to publish to (default thingsboard/.env)",
    )
    args = parser.parse_args(argv)
    try:
        window = backfill_window(args.first, args.last)
        if not args.env_file.exists():
            raise PublisherError(f"{args.env_file} not found")
        settings = load_settings(env_file=args.env_file)
        with RestClientCE(base_url=settings.tb_url) as client:
            client.login(username=settings.tb_username, password=settings.tb_password)
            # login() does not raise on bad credentials: the token just stays empty.
            if not client.token_info.get("token"):
                raise PublisherError("login failed: check TB_USERNAME/TB_PASSWORD")
            with DatabricksGold(settings) as gold:
                summary = publish(
                    settings,
                    gold,
                    Admin(client),
                    DeviceApi(settings.tb_url),
                    now_ms=int(time.time() * 1000),
                    dry_run=args.dry_run,
                    window=window,
                )
    except PublisherError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    except ApiException as exc:
        # str(exc) holds response headers and body; print only status and reason.
        print(f"FAIL ThingsBoard API error: {exc.status} {exc.reason}", file=sys.stderr)
        return 1
    except (requests.RequestException, urllib3.exceptions.HTTPError) as exc:
        print(f"FAIL cannot reach ThingsBoard: {type(exc).__name__}", file=sys.stderr)
        return 1
    report(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
