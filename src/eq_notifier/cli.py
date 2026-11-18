import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from eq_notifier import __version__
from eq_notifier.app import Alerter, make_client, run
from eq_notifier.config import ConfigError, Settings
from eq_notifier.notifiers import build_notifiers, send_all
from eq_notifier.sources import SOURCES, Source, fetch_all
from eq_notifier.state import SeenEvents
from eq_notifier.streams import Stream, build_streams

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
    commands.add_parser("run", help="watch the feeds continuously and send alerts")
    commands.add_parser(
        "check", help="poll once, probe the streams, show recent events; sends nothing"
    )
    commands.add_parser("test-notification", help="send a test message through every notifier")
    return parser


def check(alerter: Alerter, sources: Sequence[Source], streams: Sequence[Stream]) -> None:
    """Print feed health and what the alerter would do with recent earthquakes."""
    settings, now = alerter.settings, datetime.now(UTC)
    results = fetch_all(sources, alerter.client, now - settings.max_event_age, settings.bbox)
    print("Feeds:")
    for result in results:
        status = f"ERROR {result.error}" if result.error else f"ok, {len(result.events)} events"
        print(f"  {result.source:<12} {status}")
    for stream in streams:
        error = stream.probe()
        print(f"  {stream.name:<12} {f'ERROR {error}' if error else 'ok, connected'}")
    window = int(settings.max_event_age.total_seconds() // 60)
    print(f"\nEarthquakes in the last {window} min (deduplicated):")
    groups = alerter.recent_groups((e for result in results for e in result.events), now)
    if not groups:
        print("  none")
    for group in groups:
        quake = group.primary
        minutes_ago = int(quake.age(now).total_seconds() // 60)
        print(
            f"  M{quake.magnitude:.1f} {quake.region or '(no region)'} "
            f"({quake.latitude:.2f}, {quake.longitude:.2f}) {minutes_ago} min ago "
            f"via {','.join(group.sources)} - {alerter.verdict(group)}"
        )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

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
    streams = build_streams(settings)
    notifiers = build_notifiers(settings)

    with make_client() as client:
        alerter = Alerter(settings, notifiers, SeenEvents(settings.state_file), client)
        match args.command:
            case "run":
                try:
                    run(alerter, sources, streams)
                except KeyboardInterrupt:
                    log.info("Stopped")
            case "check":
                check(alerter, sources, streams)
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
