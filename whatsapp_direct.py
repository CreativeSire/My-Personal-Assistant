import pywhatkit
import datetime

# Get current time + 2 minutes (to give you time to scan QR code if needed)
now = datetime.datetime.now()
# specific fix: using timedelta to handle hour rollovers correctly
scheduled_time = now + datetime.timedelta(minutes=2)
hour = scheduled_time.hour
minute = scheduled_time.minute

# Your Phone Number (Must include Country Code, e.g., +234...)
# TODO: Update this with the real phone number before running
TARGET_PHONE = "+2349167635808" 

print(f"🤖 Victor-OS: Preparing to send WhatsApp to {TARGET_PHONE} at {hour}:{minute}...")

# Send the message
# wait_time=15 (Seconds to keep browser open)
# tab_close=True (Close tab after sending)
pywhatkit.sendwhatmsg(
    TARGET_PHONE, 
    "🤖 Victor-OS: This message was sent without Twilio!", 
    hour, 
    minute, 
    wait_time=15, 
    tab_close=True
)
