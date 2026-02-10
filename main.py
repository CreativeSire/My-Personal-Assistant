import os
import sys
from dotenv import load_dotenv
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from agents import chief_of_staff
from tools import listen_to_user

# Ensure the terminal handles UTF-8 for cleaner output
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Load environment variables
load_dotenv()
os.environ["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY")

def run_assistant():
    print("\n==================================================")
    print("       🤖 VICTOR-OS: AUTONOMOUS AGENT SYSTEM       ")
    print("==================================================")
    print("[1] ⌨️  Keyboard Mode (Type)")
    print("[2] 🎙️  God Mode (Voice Control)")
    print("--------------------------------------------------")
    
    mode_choice = input("Select Interface (1 or 2): ").strip()
    is_voice_mode = (mode_choice == '2')
    
    # Initialize Memory via the correct internal path
    session_service = InMemorySessionService()
    session_id = "victor_live_session"
    user_id = "victor"
    
    # Initialize the Runner with the required session service and app_name
    runner = Runner(
        app_name="VictorOS",
        agent=chief_of_staff, 
        session_service=session_service,
        auto_create_session=True
    )

    mode_name = "VOICE (GOD MODE)" if is_voice_mode else "TEXT"
    print(f"\n🚀 System Online in {mode_name}. (Say/Type 'exit' to quit)\n")

    while True:
        try:
            user_input = ""

            # --- INPUT PHASE ---
            if is_voice_mode:
                # Listen to microphone
                user_input = listen_to_user()
                
                # If listening failed or timed out, ask to retry or switch
                if not user_input:
                    print("...waiting for voice...")
                    continue
            else:
                # Text Mode
                user_input = input("\nYou: ")

            # --- CHECK EXIT ---
            if user_input.lower() in ["exit", "quit", "shut down"]:
                print("Victor-OS: Shutting down.")
                break

            # --- EXECUTION PHASE ---
            if user_input.strip():
                # Prepare message for the ADK Runner
                new_message = types.Content(
                    role="user",
                    parts=[types.Part(text=user_input)]
                )
                
                # Execute the chain
                events = runner.run(
                    user_id=user_id,
                    session_id=session_id, 
                    new_message=new_message
                )
                
                # Process the event stream (Generator compatible)
                print("\nAssistant: ", end="", flush=True)
                for event in events:
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                print(part.text, end="", flush=True)
                print() # New line after response

        except KeyboardInterrupt:
            print("\nVictor-OS: Force Quit.")
            break
        except Exception as e:
            print(f"\n❌ Runtime Error: {e}")

if __name__ == "__main__":
    run_assistant()
