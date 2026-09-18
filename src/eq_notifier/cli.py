import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from eq_notifier import __version__
from eq_notifier.app import CycleReport, is_relevant, make_client, poll_once, run_forever
from eq_notifier.config import ConfigError, Settings
from eq_notifier.notifiers import build_notifiers, send_all
from eq_notifier.sources import SOURCES
from eq_notifier.state import SeenEvents

log = logging.getLogger("eq_notifier")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eq-notifier",
        description="Watch public earthquake feeds and push an alert to your phone.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"), help="dotenv file to load (default: .env)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="poll continuously and send alerts")
    commands.add_parser(
        "check", help="poll once and show source health and recent events; sends nothing"
    )
    commands.add_parser("test-notification", help="send a test message through every notifier")
    return parser


def print_check_report(report: CycleReport, settings: Settings, seen: SeenEvents) -> None:
    now = datetime.now(UTC)
    print("Sources:")
    for result in report.fetched:
        status = f"ERROR {result.error}" if result.error else f"ok, {len(result.events)} events"
        print(f"  {result.source:<8} {status}")
    window = int(settings.max_event_age.total_seconds() // 60)
    print(f"\nEarthquakes in the last {window} min (deduplicated):")
    if not report.groups:
        print("  none")
    for group in report.groups:
        quake = group.primary
        if not any(is_relevant(r, settings) for r in group.reports):
            marker = "below threshold"
        elif any(r in seen for r in group.reports):
            marker = "already alerted"
        else:
            marker = "WOULD ALERT"
        minutes_ago = int(quake.age(now).total_seconds() // 60)
        print(
            f"  M{quake.magnitude:.1f} {quake.region or '(no region)'} "
            f"({quake.latitude:.2f}, {quake.longitude:.2f}) {minutes_ago} min ago "
            f"via {','.join(group.sources)} - {marker}"
        )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    load_dotenv(args.env_file)
    try:
        settings = Settings.from_env(os.environ)
        if args.command != "check" and not settings.notifiers:
            raise ConfigError(
                "EQ_NOTIFIERS must name at least one notifier (ntfy, telegram, twilio)"
            )
    except ConfigError as exc:
        parser.exit(2, f"{exc}\n")

    sources = [SOURCES[name] for name in settings.sources]
    notifiers = build_notifiers(settings)

    with make_client() as client:
        match args.command:
            case "run":
                seen = SeenEvents(settings.state_file)
                try:
                    run_forever(settings, sources, notifiers, seen, client)
                except KeyboardInterrupt:
                    log.info("Stopped")
            case "check":
                seen = SeenEvents(settings.state_file)
                report = poll_once(settings, sources, notifiers, seen, client, dry_run=True)
                print_check_report(report, settings, seen)
            case "test-notification":
                delivered = send_all(
                    notifiers,
                    client,
                    "eq-notifier test",
                    "Test notification. If you can read this, earthquake alerts will reach you.",
                )
                if not delivered:
                    sys.exit("Test notification failed on every notifier; see the log above.")
                print(f"Delivered via: {', '.join(delivered)}")
