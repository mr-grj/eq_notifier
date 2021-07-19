"""
This projects aims to send an SMS to a specific number using Twilio
if an earthquake with magnitude > 4 is going to occur (depending on
your location, this can warn you (best case scenario within 25-30
seconds before you feel the earthquake wave).
"""

from argparse import ArgumentParser
from datetime import datetime
from functools import partial
from json import load, loads
from re import search
from time import sleep
from typing import Dict

from lxml.html import fromstring
from requests import Session
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

BASE_URL = 'http://alerta.infp.ro'
DATA_URL = f'{BASE_URL}/server.php'


def command_line_parser() -> ArgumentParser:
    """Simple command line parser function."""

    parser = ArgumentParser(description='Earthquake Listener')
    parser.add_argument(
        '-t', '--twilio',
        nargs='?',
        type=credentials,
        const='credentials.json',
        help='Path to the credentials file'
    )
    parser.add_argument(
        '-d', '--delay',
        type=float,
        default=1.0,
        help='Delay in seconds between two calls to the earthquake API'
    )
    return parser


def credentials(filepath: str) -> Dict[str, str]:
    """Return secrets from `filepath` file as a dict.

    The file looks like this:

        {
          "TWILIO_ACCOUNT_SID": "Your twilio account SID",
          "TWILIO_AUTH_TOKEN": "Your twilio account auth token",
          "FROM": "The number from which you'll receive the alert",
          "TO": "The number the message is sent to"
        }

    Args:
        filepath (str): Path to credentials JSON file.

    Returns:
        dict: The return value.
    """

    with open(filepath) as credentials_file:
        credentials_data = load(credentials_file)

    if not credentials_data:
        raise ValueError('Credentials file should not be empty.')

    return credentials_data


def get_earthquake_data() -> Dict[str, str]:
    """Get earthquake data from `DATA_URL`.

    Returns:
        dict: A dict containing the following data:
              {
                'mag': '0.1',
                'heart': '2020-01-04 13:30:04 HEARTBEAT',
                'sec': '30',
                'key': 'NjY2NDYyMzAzNjMwNjM2MzM1Mz...=='
              }
    """

    session = Session()
    with session as page_session:
        html_page = page_session.get(BASE_URL).content
        html_script = fromstring(html_page).xpath('//script[contains(., "source")]/text()')[0]
        key = {
            'keyto': search(
                r"var source = new EventSource\('server\.php\?keyto=(.*)'\);", html_script
            ).group(1)
        }
        earthquake_data = page_session.get(f'{DATA_URL}', params=key).content
        earthquake_data = earthquake_data.decode('utf8').replace("data", '"data"').strip()
        return loads(f'{{{earthquake_data}}}')


def send_message(
        twilio_client: Client,
        send_to: str,
        sent_from: str,
        max_magnitude: float = 4.0
) -> None:
    """Send a message via Twilio if the magnitude of an earthquake
    is bigger than 4.0.
    """

    data = get_earthquake_data().get('data')
    eq_magnitude = data.get('mag')

    if float(eq_magnitude) >= max_magnitude:
        body = f"""ATTENTION!!!

        Earthquake with magnitude: {eq_magnitude}
        at {datetime.strftime(datetime.now(), '%Y-%m-%d %H:%M:%S')}!
        """

        try:
            twilio_client.messages.create(body=body, from_=sent_from, to=send_to)
        except TwilioRestException as error:
            print(f'Twilio API error: {error}')
    else:
        print('No need to worry. YET!')


def main(credentials_data: Dict[str, str] = None, delay: float = 1.0) -> None:
    """Main entry to the program."""

    if credentials_data is None:
        action = get_earthquake_data
    else:
        twilio_client = Client(
            credentials_data['TWILIO_ACCOUNT_SID'],
            credentials_data['TWILIO_AUTH_TOKEN']
        )
        sender = credentials_data['FROM']
        receiver = credentials_data['TO']
        action = partial(send_message, twilio_client, receiver, sender)

    while True:
        try:
            action()
            sleep(delay)
        except KeyboardInterrupt:
            print('Closing the program...')
            break


if __name__ == '__main__':
    ARGS = command_line_parser().parse_args()
    main(ARGS.twilio, ARGS.delay)
