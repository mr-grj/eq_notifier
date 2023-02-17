from argparse import ArgumentParser
from earthquake_listener import EarthquakeListener


def command_line_parser() -> ArgumentParser:
    """Simple command line parser function."""
    parser = ArgumentParser(description="Earthquake Listener")
    parser.add_argument(
        "-t", "--twilio", nargs="?", type=str, help="Path to the credentials file"
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between two calls to the earthquake API",
    )
    parser.add_argument(
        "-m",
        "--max-magnitude",
        type=float,
        default=4.0,
        help="Maximum earthquake magnitude to trigger an alert",
    )
    return parser


if __name__ == "__main__":
    args = command_line_parser().parse_args()

    if args.twilio:
        credentials = EarthquakeListener.load_credentials(args.twilio)
        listener = EarthquakeListener(
            twilio_account_sid=credentials["TWILIO_ACCOUNT_SID"],
            twilio_auth_token=credentials["TWILIO_AUTH_TOKEN"],
            from_number=credentials["FROM"],
            to_number=credentials["TO"],
            delay=args.delay,
            max_magnitude=args.max_magnitude,
        )
    else:
        listener = EarthquakeListener(
            delay=args.delay, max_magnitude=args.max_magnitude
        )

    listener.run()
