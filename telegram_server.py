import telebot
import os
import sys
from tendo import singleton

# --- SINGLETON LOCK (Prevent Duplicate Instances) ---
try:
    me = singleton.SingleInstance()
except singleton.SingleInstanceException:
    with open("victor_os/server_debug.txt", "a") as f:
        f.write(f"[{os.getpid()}] Singleton lock - exiting.\n")
    sys.exit(0)

# --- STARTUP LOG ---
with open("victor_os/server_debug.txt", "a") as f:
    f.write(f"\n--- SERVER STARTUP [{os.getpid()}] ---\n")

from google.adk import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from agents import (
    chief_of_staff, 
    research_agent, 
    dev_agent, 
    data_agent, 
    script_agent, 
    academic_agent
)
from tools import transcribe_audio_file, generate_tts_file
from monitor import log_activity


# --- CONFIGURATION ---
BOT_TOKEN = "7770925936:AAFfZs38EmdCsS8BUS5x2LT3kNV6on5AdzY"

print("🔌 Connecting to Telegram...")
bot = telebot.TeleBot(BOT_TOKEN)
session_service = SqliteSessionService(db_path="victor_os/memory_store/victor_memory.db")

# Initialize Runner
runner = Runner(
    agent=chief_of_staff, 
    session_service=session_service,
    app_name="victor_os",
    auto_create_session=True
)

print(f"🤖 Victor-OS (Multimodal Mode) is Online! (Bot ID: {BOT_TOKEN.split(':')[0]})")

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "🚀 Victor-OS is Online. I can read your texts, hear your voice, and analyze your photos!")

# --- SAFETY VALVE (Auto-Splitter) ---
def send_smart_message(chat_id, text, reply_to_id=None):
    """Splits messages longer than 4000 chars and sends them in parts."""
    MAX_LENGTH = 4000
    if len(text) <= MAX_LENGTH:
        if reply_to_id:
            return bot.reply_to(reply_to_id, text)
        else:
            return bot.send_message(chat_id, text)
    
    # Split into chunks
    chunks = [text[i:i + MAX_LENGTH] for i in range(0, len(text), MAX_LENGTH)]
    total = len(chunks)
    
    for i, chunk in enumerate(chunks):
        formatted_chunk = f"📄 (Part {i+1}/{total})\n\n{chunk}"
        if i == 0 and reply_to_id:
            bot.reply_to(reply_to_id, formatted_chunk)
        else:
            bot.send_message(chat_id, formatted_chunk)
    return True

# --- VOICE HANDLER ---
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    user_id = str(message.chat.id)
    print(f"\n🎙️ INCOMING VOICE from {message.from_user.first_name}")
    bot.send_chat_action(message.chat.id, 'record_audio')

    try:
        log_activity("Telegram User", "Voice Note Received", "Info")
        # 1. Download the voice file
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        voice_path = f"voice_{message.chat.id}.ogg"
        with open(voice_path, 'wb') as new_file:
            new_file.write(downloaded_file)
        
        # 2. Transcribe
        print("⏳ Transcribing...")
        user_text = transcribe_audio_file(voice_path)
        
        if not user_text or user_text.startswith("❌"):
            bot.reply_to(message, "⚠️ Sorry, I couldn't understand that audio.")
            return
    
        print(f"🗣️ Transcribed: '{user_text}'")
        
        # 3. Process as normal message
        process_message(message, user_text)

        # Cleanup
        if os.path.exists(voice_path): os.remove(voice_path)
        wav_path = voice_path.replace(".ogg", ".wav")
        if os.path.exists(wav_path): os.remove(wav_path)

    except Exception as e:
        print(f"❌ Voice Error: {e}")
        bot.reply_to(message, f"Voice System Error: {e}")

# --- PHOTO HANDLER ---
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user_id = str(message.chat.id)
    caption = message.caption if message.caption else "Analyze this image."
    print(f"\n🖼️ INCOMING PHOTO from {message.from_user.first_name}: {caption}")
    bot.send_chat_action(message.chat.id, 'upload_photo')

    try:
        log_activity("Telegram User", "Photo Received", "Info", f"Caption: {caption}")
        # 1. Get the largest photo
        file_info = bot.get_file(message.photo[-1].file_id)
        image_data = bot.download_file(file_info.file_path)

        # 2. Process
        process_message(message, caption, image_data=image_data)

    except Exception as e:
        print(f"❌ Photo Error: {e}")
        bot.reply_to(message, f"Vision System Error: {e}")

# --- DOCUMENT HANDLER (Universal Downloader) ---
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = str(message.chat.id)
    file_name = message.document.file_name
    print(f"\n📁 INCOMING DOCUMENT from {message.from_user.first_name}: {file_name}")
    bot.send_chat_action(message.chat.id, 'upload_document')
    
    try:
        log_activity("Telegram User", "Document Received", "Info", f"File: {file_name}")
        
        # 1. Ensure workspace exists
        workspace_dir = "victor_os/workspace"
        if not os.path.exists(workspace_dir):
            os.makedirs(workspace_dir)
            
        # 2. Download the file
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_path = os.path.join(workspace_dir, file_name)
        with open(file_path, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        # 3. Inform the Agent
        system_note = f"User uploaded a file available at: {file_path}. Use Python to read/analyze it if requested."
        user_text = message.caption if message.caption else f"I've uploaded {file_name}."
        combined_text = f"{user_text}\n\n[SYSTEM NOTE: {system_note}]"
        
        process_message(message, combined_text)
        
    except Exception as e:
        print(f"❌ Document Error: {e}")
        bot.reply_to(message, f"File System Error: {e}")

# --- TEXT HANDLER ---
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    process_message(message, message.text)

def process_message(message, user_text, image_data=None):
    user_id = str(message.chat.id)
    print(f"\n📩 PROCESSING: {user_text} {'(with image)' if image_data else ''}")
    bot.send_chat_action(message.chat.id, 'typing')
    log_activity("Telegram User", "Input Received", "Info", f"Text: {user_text[:50]}...")

    try:
        # 1. Prepare Content Object
        # --- MEMORY INJECTION (RAG) ---
        import sqlite3
        import json
        
        # --- NEW: VECTOR VAULT (God Mode) ---
        try:
            from memory_core import recall_long_term_memory
            vault_context = recall_long_term_memory(user_text)
            print(f"🧠 VAULT RECALL: {len(vault_context)} chars")
        except Exception as e:
            print(f"⚠️ Vector Core Error (Offline): {e}")
            vault_context = "Vector Vault Offline."

        history_context = ""
        try:
            conn = sqlite3.connect("victor_os/memory_store/victor_memory.db")
            cursor = conn.cursor()
            
            # Fetch last 20 events (generous window to get context)
            cursor.execute(f"SELECT event_data FROM events WHERE session_id='{user_id}' ORDER BY timestamp DESC LIMIT 20")
            rows = cursor.fetchall()
            messages = []
            
            for row in rows:
                try:
                    event_json = row[0]
                    data = json.loads(event_json)
                    
                    # Extract Dialog Content
                    if "content" in data and "parts" in data["content"]:
                        role = data["content"]["role"] # 'user' or 'model'
                        text = ""
                        for part in data["content"]["parts"]:
                            if "text" in part:
                                text += part["text"]
                        
                        if text:
                            label = "User" if role == "user" else "Victor"
                            messages.append(f"{label}: {text}")
                            
                except Exception as json_err:
                    continue # Skip malformed events

            if messages:
                # Reverse to chronological order (we fetched DESC)
                history_str = "\n".join(reversed(messages))
                history_context = f"SYSTEM: Here is the recent conversation history from the database. USE THIS to answer. Do not say you don't remember if it is written here.\n\n[HISTORY LOG]\n{history_str}\n\n"
                print(f"🧠 RAG INJECTED ({len(messages)} turns)")
            else:
                print("🧠 RAG: No history found in DB.")
            
            conn.close()
        except Exception as e:
            print(f"⚠️ RAG Injection Failed: {e}")

        # Prepend history AND Vault to user text
        # We combine them: 1. Deep Memory (Vault), 2. Recent History (Context), 3. User Input
        
        full_system_block = f"""
=== 🧠 LONG-TERM MEMORY RETRIEVAL (THE VAULT) ===
{vault_context}
=================================================

{history_context}
"""
        full_prompt = full_system_block + "USER: " + user_text

        parts = [types.Part(text=full_prompt)]
        if image_data:
            parts.append(types.Part.from_bytes(data=image_data, mime_type="image/jpeg"))

        new_message = types.Content(
            role="user",
            parts=parts
        )

        # 2. Start the Stream
        event_stream = runner.run(
            user_id="telegram_user", 
            session_id=user_id, 
            new_message=new_message
        )
        
        final_answer = ""
        tool_called = False
        with open("victor_os/server_debug_stream.txt", "a", encoding="utf-8") as f:
            f.write(f"\n--- EVENT STREAM START ({user_text}) ---\n")
            f.write(f"DEBUG: new_message='{new_message}'\n")
            
        try:
            for event in event_stream:
                # --- DEBUG LOGGING ---
                with open("victor_os/server_debug_stream.txt", "a", encoding="utf-8") as f:
                    f.write(f"EVENT_TYPE: {type(event)}\n")
                
                # 1. Capture Tool Calls (Metadata)
                if hasattr(event, 'tool_calls') and event.tool_calls:
                    tool_called = True
                    continue
                
                # 2. Extract Text (The Core Task)
                chunk_text = ""
                
                # Check A: Direct text attribute
                if hasattr(event, 'text') and event.text:
                    chunk_text = event.text
                
                # Check B: Content parts
                elif hasattr(event, 'content') and event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, 'text') and part.text:
                            chunk_text += part.text
                            
                # Check C: Candidates (Deep structure)
                elif hasattr(event, 'candidates') and event.candidates:
                    for candidate in event.candidates:
                        if hasattr(candidate, 'content') and candidate.content and candidate.content.parts:
                            for part in candidate.content.parts:
                                if hasattr(part, 'text') and part.text:
                                    chunk_text += part.text

                # 3. Append to Final Answer
                if chunk_text:
                    # WE REMOVED THE JSON FILTER. 
                    # Trust the model. If it outputs text, it goes to the user.
                    final_answer += chunk_text
                    
        except Exception as e:
             with open("victor_os/server_debug_stream.txt", "a", encoding="utf-8") as f:
                f.write(f"ERROR: Event Loop Failed: {e}\n")

        # 3. Decision: Text or Voice Reply?
        should_speak = "speak" in user_text.lower() or "say" in user_text.lower()
        
        # 4. Final Fallback Logic (The Safety Net)
        if not final_answer.strip():
            if tool_called:
                # If tools ran but no text, assume success.
                final_answer = "✅ Actions executed successfully."
            else:
                # If absolutely nothing happened, the model failed to generate.
                final_answer = "⚠️ I received your input, but I was unable to generate a response. Please check the system logs."

        if should_speak:
            print("🔊 Generating Voice Response...")
            audio_path = generate_tts_file(final_answer, f"reply_{user_id}.mp3")
            if not audio_path.startswith("❌"):
                with open(audio_path, 'rb') as audio:
                    bot.send_voice(message.chat.id, audio)
                os.remove(audio_path)
                log_activity("Chief_of_Staff", "Voice Response Sent", "Success")
            else:
                send_smart_message(message.chat.id, final_answer, reply_to_id=message)
                log_activity("Chief_of_Staff", "Text Response Sent (Voice Failed)", "Success")
        else:
            # --- COURIER PROTOCOL (Auto-Upload) ---
            import re
            file_match = re.search(r"<<SEND_FILE:\s*(.*?)>>", final_answer)
            
            if file_match:
                raw_path = file_match.group(1).strip()
                clean_answer = final_answer.replace(file_match.group(0), "").strip()
                
                if clean_answer:
                    send_smart_message(message.chat.id, clean_answer, reply_to_id=message)
                
                print(f"📦 COURIER: Shipping {raw_path}...")
                if os.path.exists(raw_path):
                    with open(raw_path, 'rb') as doc:
                        bot.send_document(message.chat.id, doc)
                    log_activity("Chief_of_Staff", "Courier Successful", "Success", f"File: {raw_path}")
                else:
                    send_smart_message(message.chat.id, f"❌ Courier Error: File not found at {raw_path}", reply_to_id=message)
                    log_activity("Chief_of_Staff", "Courier Failed", "Error", f"Missing: {raw_path}")
            else:
                send_smart_message(message.chat.id, final_answer, reply_to_id=message)
                log_activity("Chief_of_Staff", "Text Response Sent", "Success")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        bot.reply_to(message, f"System Error: {e}")
        log_activity("System", "Error", "Failed", str(e))

bot.infinity_polling(timeout=60, long_polling_timeout=60)