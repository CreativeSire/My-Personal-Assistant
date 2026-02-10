import time
import datetime
import schedule
import telebot
from dotenv import load_dotenv
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

# Load environment
load_dotenv()

# --- CONFIGURATION ---
BOT_TOKEN = "7770925936:AAFfZs38EmdCsS8BUS5x2LT3kNV6on5AdzY"
TARGET_TELEGRAM_ID = "1322285119"

bot = telebot.TeleBot(BOT_TOKEN)
session_service = InMemorySessionService()

# Initialize Runner with the Chef (Root)
runner = Runner(
    app_name="VictorOS_Briefing",
    agent=chief_of_staff,
    session_service=session_service,
    auto_create_session=True
)

def job_morning_briefing():
    print(f"🌅 Waking up... Preparing Daily Briefing for {TARGET_TELEGRAM_ID}...")
    
    # 1. The Mission: Gather Intelligence
    # We ask the Chief to coordinate this.
    prompt = """
    MORNING BRIEFING TASK:
    1. Research the current price of Bitcoin and Ethereum (Live).
    2. Research one major AI news headline from the last 24 hours.
    3. Research one key business headline from Lagos, Nigeria.
    
    Format the results as a short report with emojis. 
    Maintain a professional but visionary tone.
    """

    try:
        new_message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)]
        )

        # 2. Run the agent stream
        events = runner.run(
            user_id="briefing_system", 
            session_id="daily_briefing_session",
            new_message=new_message
        )
        
        # Collect response text
        text_content = ""
        for event in events:
            # 1. Check for basic .text
            if hasattr(event, 'text') and event.text:
                text_content += event.text
            # 2. Check for standard Event.content.parts
            elif event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        text_content += part.text
            # 3. Check for deep structure candidates
            elif hasattr(event, 'candidates') and event.candidates:
                for candidate in event.candidates:
                    if hasattr(candidate, 'content') and candidate.content:
                        for part in candidate.content.parts:
                             if hasattr(part, 'text') and part.text:
                                text_content += part.text

        if not text_content:
            text_content = "⚠️ Morning briefing research returned no data. Check internet connection."

        message = f"🤖 *Victor-OS Morning Briefing*\n{'-'*30}\n\n{text_content}"
        
        # 3. Send via Telegram Bot API
        print(f"🚀 Pushing to Telegram ID: {TARGET_TELEGRAM_ID}...")
        bot.send_message(TARGET_TELEGRAM_ID, message)
        print("✅ Briefing Delivered.")

    except Exception as e:
        print(f"❌ Briefing Execution Error: {e}")

# --- THE SCHEDULE ---
# This runs the job every day at 08:00 AM
schedule.every().day.at("08:00").do(job_morning_briefing)

# --- TEST MODE ---
# Triggering immediately for verification as requested by the plan
print("🔧 Initializing System Check: Running first briefing...")
job_morning_briefing() 

print(f"🤖 Victor-OS Scheduler is Online. Monitoring for 8:00 AM... (Target: {TARGET_TELEGRAM_ID})")
while True:
    schedule.run_pending()
    time.sleep(60)
