import json
from datetime import datetime
from json import load
from time import sleep
from typing import Dict, Optional

import requests
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client


class EarthquakeListener:
    BASE_URL = 'http://alerta.infp.ro'
    DATA_URL = f'{BASE_URL}/server.php'

    def __init__(
            self,
            twilio_account_sid: Optional[str] = None,
            twilio_auth_token: Optional[str] = None,
            from_number: Optional[str] = None,
            to_number: Optional[str] = None,
            delay: float = 1.0,
            max_magnitude: float = 4.0,
    ):
        self.twilio_client = None
        if twilio_account_sid and twilio_auth_token and from_number and to_number:
            self.twilio_client = Client(twilio_account_sid, twilio_auth_token)
        self.from_number = from_number
        self.to_number = to_number
        self.delay = delay
        self.max_magnitude = max_magnitude

    @staticmethod
    def load_credentials(filepath: str) -> Dict[str, str]:
        """Load Twilio credentials from file."""
        with open(filepath) as f:
            return load(f)

    def get_earthquake_data(self) -> Dict[str, str]:
        """Get earthquake data from `DATA_URL`."""
        try:
            response = requests.get(self.DATA_URL)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f'Error fetching earthquake data: {e}')
            return {}

        response_text = response.text.lstrip('data: ')
        try:
            return json.loads(response_text)
        except ValueError as e:
            print(f'Error decoding earthquake data: {e}')
            return {}

    def send_message(self, data: Dict[str, str]) -> None:
        """Send a message via Twilio if the magnitude of an earthquake
        is greater than the specified threshold.
        """
        if not data:
            print('No earthquake data found')
            return

        eq_magnitude = data.get('mag')
        if not isinstance(eq_magnitude, (int, float)):
            print(f'Invalid earthquake magnitude: {eq_magnitude}')
            return

        if eq_magnitude <= self.max_magnitude:
            print(f'No need to worry. Magnitude: {eq_magnitude}')
            return

        if not self.twilio_client:
            print('Twilio credentials not set')
            return

        body = (
            f"ATTENTION!!!\n"
            f"Earthquake with magnitude: {eq_magnitude}\n"
            f"at {datetime.now():%Y-%m-%d %H:%M:%S}!"
        )
        try:
            self.twilio_client.messages.create(body=body, from_=self.from_number, to=self.to_number)
        except TwilioRestException as e:
            print(f'Twilio API error: {e}')

    def run(self) -> None:
        """Main loop."""
        while True:
            data = self.get_earthquake_data()
            self.send_message(data)
            sleep(self.delay)
