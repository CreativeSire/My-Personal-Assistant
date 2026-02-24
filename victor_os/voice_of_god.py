"""
Project Voice of God
Twilio-based voice interface for Victor OS.
"""

from twilio.rest import Client
from victor_os.config import get_config

cfg = get_config()

class VoiceOfGod:
    def __init__(self):
        self.client = Client(cfg.twilio_sid, cfg.twilio_auth_token) if cfg.twilio_sid else None
        self.enabled = cfg.twilio_voice_enabled and self.client is not None

    def call_user(self, message: str):
        if not self.enabled:
            print("Voice of God disabled or Twilio not configured.")
            return

        print(f"Calling user: {cfg.my_phone_number}")
        # Groundwork for Twilio Call
        # self.client.calls.create(
        #     twiml=f'<Response><Say>{message}</Say></Response>',
        #     to=cfg.my_phone_number,
        #     from_=cfg.twilio_whatsapp_number # Usually a voice-enabled Twilio number
        # )
