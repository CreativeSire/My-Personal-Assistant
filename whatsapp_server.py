from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from agents import (
    chief_of_staff, 
    research_agent, 
    dev_agent, 
    data_agent, 
    script_agent, 
    academic_agent
)
import os
import sys
from dotenv import load_dotenv

# Load env variables (API keys, etc.)
load_dotenv()

app = Flask(__name__)

# Initialize Memory
session_service = InMemorySessionService()

# Initialize Runner
# Importing all agents ensures the Runner has context for handoffs
runner = Runner(
    app_name="VictorOS_WhatsApp",
    agent=chief_of_staff, 
    session_service=session_service,
    auto_create_session=True
)

@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    """Handles incoming WhatsApp messages via Twilio webhook."""
    # 1. Get the message YOU sent on WhatsApp
    incoming_msg = request.values.get('Body', '').strip()
    sender = request.values.get('From', '')
    
    print(f"📩 WhatsApp from {sender}: {incoming_msg}")

    # 2. Run Victor-OS (Chief of Staff)
    new_message = types.Content(
        role="user",
        parts=[types.Part(text=incoming_msg)]
    )

    try:
        # Execute the agent
        events = runner.run(
            user_id="whatsapp_user", 
            session_id=sender,       
            new_message=new_message
        )
        
        # Aggregate the streaming response
        result_text = ""
        for event in events:
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        result_text += part.text
        
        # Fallback if no text generated
        if not result_text:
            result_text = "✅ Task completed (No further text needed)."

    except Exception as e:
        print(f"❌ Error during execution: {e}")
        result_text = f"❌ Victor-OS Error: {e}"

    # 3. Send Victor's reply back to WhatsApp
    resp = MessagingResponse()
    msg = resp.message()
    msg.body(result_text)
    
    return str(resp)

if __name__ == "__main__":
    print("🤖 Victor-OS WhatsApp Server Running on Port 5000...")
    app.run(port=5000)
