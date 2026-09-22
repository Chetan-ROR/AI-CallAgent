import os

from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN"),
    http_client=TwilioHttpClient(timeout=8),
)
