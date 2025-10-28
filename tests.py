import json
from unittest.mock import patch

from earthquake_listener import EarthquakeListener


def test_load_credentials(tmp_path):
    credentials_data = {
        'TWILIO_ACCOUNT_SID': 'test_sid',
        'TWILIO_AUTH_TOKEN': 'test_token',
        'FROM': 'test_from',
        'TO': 'test_to'
    }
    credentials_file = tmp_path / 'credentials.json'
    with open(credentials_file, 'w') as f:
        json.dump(credentials_data, f)
    loaded_data = EarthquakeListener.load_credentials(str(credentials_file))
    assert loaded_data == credentials_data


def test_get_earthquake_data():
    response_text = 'data: {"mag":"1.0","heart":"2023-02-16 13:47:41 HEARTBEAT","sec":"30"}'
    expected_data = {'mag': '1.0', 'heart': '2023-02-16 13:47:41 HEARTBEAT', 'sec': '30'}
    with patch('requests.get') as mock_get:
        mock_get.return_value.text = response_text
        mock_get.return_value.status_code = 200
        listener = EarthquakeListener()
        data = listener.get_earthquake_data()
        assert data == expected_data


def test_send_message():
    listener = EarthquakeListener(
        twilio_account_sid='test_sid',
        twilio_auth_token='test_token',
        from_number='test_from',
        to_number='test_to'
    )
    with patch('twilio.rest.Client') as mock_client:
        listener.send_message({'mag': '1.0'})
        mock_client.assert_called_once_with('test_sid', 'test_token')
