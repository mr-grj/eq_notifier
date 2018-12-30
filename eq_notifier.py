import datetime
import json
import re
import requests
import time

from lxml.html import fromstring
from twilio.rest import Client


BASE_URL = 'http://alerta.infp.ro'
DATA_URL = f'{BASE_URL}/server.php'


class EarthquakeData:
    def __init__(self):
        self.session = requests.Session()

    @staticmethod
    def get_secrets():
        """
        Get secrets from `credentials.json` file. The file looks like this:

        {
          "TWILIO_ACCOUNT_SID": "Your twilio account sid",
          "TWILIO_AUTH_TOKEN": "Your twilio account auth token",
          "FROM": "The number from which you'll receive the alert",
          "TO": "The number the message is sent to"
        }
        """

        with open('credentials.json') as credentials_file:
            data = json.load(credentials_file)
        return data

    def get_eq_data(self):
        """
        Get earthquake information from `http://alerta.infp.ro` as a string.
        """

        with self.session as session:
            try:
                html_page = session.get(BASE_URL).content
            except requests.exceptions.ConnectionError:
                time.sleep(10)
                html_page = session.get(BASE_URL).content

            html_script = fromstring(html_page).xpath('//script[contains(., "source")]/text()')[0]
            key = {
                'keyto': re.search(
                    r"var source = new EventSource\('server\.php\?keyto=(.*)'\);", html_script
                ).group(1)
            }
            return session.get(f'{DATA_URL}', params=key).content

    def transform_eq_data(self):
        """
        Convert eq data to a dictionary.
        """

        data = self.get_eq_data().decode('utf8')
        data = data.replace("data", '"data"').strip()
        return json.loads(f'{{{data}}}')

    def send_message(self):
        twilio_client = Client(
            self.get_secrets().get('TWILIO_ACCOUNT_SID'),
            self.get_secrets().get('TWILIO_AUTH_TOKEN')
        )

        data = self.transform_eq_data().get('data')
        eq_magnitude = data.get('mag')

        if float(eq_magnitude) >= 4:
            body = f"""ATTENTION!!!
            
            Earthquake with magnitude: {eq_magnitude} 
            at {datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')}!
            """
            try:
                twilio_client.messages.create(
                    body=body, from_=self.get_secrets().get('FROM'), to=self.get_secrets().get('TO')
                )
            except Exception as e:
                print(f'Twilio API error: {e}')


def main():
    while True:
        eq = EarthquakeData()
        try:
            eq.send_message()
            time.sleep(15)
        except TypeError as e:
            print(e)
            continue


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Closing...')
