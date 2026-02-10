import os
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pandas as pd
import numpy as np
from textblob import TextBlob
from sklearn.feature_extraction.text import CountVectorizer
import pyreadstat
import pyttsx3
import matplotlib.pyplot as plt
import speech_recognition as sr
from io import StringIO
from pypdf import PdfReader
from docx import Document
from dotenv import load_dotenv
from pydub import AudioSegment
import pyautogui

# Load keys
load_dotenv()
MY_EMAIL = os.getenv("EMAIL_USER")
MY_PASS = os.getenv("EMAIL_PASS")
MY_TARGET = os.getenv("TO_EMAIL")

# --- TOOL 1: THE UNIVERSAL READER ---
def universal_file_reader(file_path: str) -> str:
    """Reads Excel, CSV, PDF, Docx, SPSS (.sav), and Stata (.dta)."""
    try:
        clean_path = file_path.strip().strip('"').strip("'")
        if not os.path.exists(clean_path):
            return f"❌ Error: File not found at {clean_path}"

        ext = clean_path.strip().split('.')[-1].lower()

        if ext in ['xlsx', 'xls', 'csv']:
            if ext == 'csv': df = pd.read_csv(clean_path)
            else: df = pd.read_excel(clean_path)
            return f"--- DATA ({ext}) ---\nColumns: {list(df.columns)}\nRows: {df.shape[0]}\nSample:\n{df.head(20).to_string()}\n--- END ---"
        elif ext == 'sav':
            df, meta = pyreadstat.read_sav(clean_path)
            labels = getattr(meta, 'column_names_to_labels', 'N/A')
            return f"--- SPSS DATA (.sav) ---\nLabels: {labels}\nRows: {df.shape[0]}\nSample:\n{df.head(20).to_string()}\n--- END ---"
        elif ext == 'dta':
            df, meta = pyreadstat.read_dta(clean_path)
            return f"--- STATA DATA (.dta) ---\nRows: {df.shape[0]}\nSample:\n{df.head(20).to_string()}\n--- END ---"
        elif ext == 'pdf':
            reader = PdfReader(clean_path)
            text = "".join([(p.extract_text() or "") + "\n" for p in reader.pages])
            return f"--- PDF --- {text[:50000]} ... --- END ---"
        elif ext == 'docx':
            doc = Document(clean_path)
            text = "\n".join([p.text for p in doc.paragraphs])
            return f"--- DOCX --- {text} --- END ---"
        else:
            with open(clean_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f"--- TEXT --- {f.read()} --- END ---"
    except Exception as e:
        return f"❌ Error reading file: {e}"

# --- TOOL 2: THE EXECUTION ENGINE ---
def execute_python_code(code: str) -> str:
    """Executes Python code. Supports pandas, numpy, textblob, sklearn."""
    clean_code = code.replace("```python", "").replace("```", "").strip()
    old_stdout = sys.stdout
    redirected_output = StringIO()
    sys.stdout = redirected_output
    
    try:
        local_scope = {
            'pd': pd,
            'np': np,
            'TextBlob': TextBlob,
            'CountVectorizer': CountVectorizer,
            'plt': plt,
            'os': os
        }
        exec(clean_code, globals(), local_scope)
        sys.stdout = old_stdout
        return f"✅ Execution Successful.\nOutput:\n{redirected_output.getvalue()}"
    except Exception as e:
        sys.stdout = old_stdout
        return f"❌ Execution Error: {e}"

# --- TOOL 3: THE VOICE ---
def speak_out_loud(text: str) -> str:
    """Reads text out loud."""
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 170) 
        engine.say(text)
        engine.runAndWait()
        return "✅ Audio played successfully."
    except Exception as e:
        return f"❌ Audio Error: {e}"

def generate_tts_file(text: str, filename: str = "response.mp3") -> str:
    """Generates a TTS audio file and returns the path."""
    try:
        engine = pyttsx3.init()
        engine.save_to_file(text, filename)
        engine.runAndWait()
        return os.path.abspath(filename)
    except Exception as e:
        return f"❌ TTS Generation Error: {e}"

# --- TOOL 4: THE LISTENER ---
def listen_to_user() -> str:
    """Activates microphone and returns text."""
    r = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            print("\n👂 Listening... (Speak now!)")
            r.adjust_for_ambient_noise(source, duration=1)
            audio = r.listen(source, timeout=5, phrase_time_limit=15)
            
            print("⏳ Processing...")
            text = r.recognize_google(audio)
            print(f"🗣️ You said: '{text}'")
            return text
            
    except Exception as e:
        # Graceful fallback for PyAudio/Mic issues
        return ""

def transcribe_audio_file(file_path: str) -> str:
    """Transcribes an audio file (ogg, wav, mp3) to text."""
    r = sr.Recognizer()
    try:
        ext = file_path.split('.')[-1].lower()
        if ext != 'wav':
            # Convert to wav for speech_recognition
            audio = AudioSegment.from_file(file_path)
            wav_path = file_path.replace(f".{ext}", ".wav")
            audio.export(wav_path, format="wav")
            file_path = wav_path

        with sr.AudioFile(file_path) as source:
            audio_data = r.record(source)
            text = r.recognize_google(audio_data)
            return text
    except Exception as e:
        return f"❌ Transcription Error: {e}"

# --- TOOL 5: THE OUTBOX (Email) ---
def send_email_alert(subject: str, body: str) -> str:
    """
    Sends an email to Victor's primary address. 
    Use this for sending finished reports, urgent alerts, or reminders.
    """
    try:
        msg = MIMEText(body)
        msg['Subject'] = f"🤖 Victor-OS: {subject}"
        msg['From'] = MY_EMAIL
        msg['To'] = MY_TARGET

        # Connect to Gmail Server
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(MY_EMAIL, MY_PASS)
            server.send_message(msg)
        
        return "✅ Email sent successfully."
    except Exception as e:
        return f"❌ Email failed: {e}"


# --- TOOL 6: THE SYSTEM GUARDIAN ---
def run_system_diagnostic() -> str:
    """Checks CPU, RAM, Disk, and Dev-Port health (3000, 8000, 8501)."""
    try:
        from monitor import get_system_metrics
        m = get_system_metrics()
        
        status = "🟢 NOMINAL" if m['cpu'] < 80 and m['ram'] < 90 else "🔴 STRESSED"
        
        report = f"--- 🖥️ SYSTEM HEALTH: {status} ---\n"
        report += f"🚀 CPU: {m['cpu']}%\n"
        report += f"🧠 RAM: {m['ram']}%\n"
        report += f"💾 DISK: {m['disk']}%\n"
        report += "\n📡 PORT STATUS:\n"
        for port, state in m.get('ports', {}).items():
            icon = "✅" if state == "OPEN" else "❌"
            report += f" - {port}: {state} {icon}\n"
        report += "--- END DIAGNOSTIC ---"
        return report
    except Exception as e:
        return f"❌ Diagnostic Error: {e}"


# --- TOOL 7: THE CODE ARCHITECT ---
def review_code(code: str) -> str:
    """Analyzes code quality, security, and performance."""
    try:
        from monitor import log_activity
        log_activity("Code Architect", "Code Analysis Triggered", "Info", f"Snippet length: {len(code)}")
        
        # This is a specialized system prompt for code analysis
        # In a real tool, this would be a specific LLM call, but here we provide a structured template
        # that the Agent can populate when it uses the 'execute_python_code' or handles text.
        # But wait, the Agent is the one calling this tool. The tool itself should provide
        # a checklist or a framework for the Agent to use.
        
        analysis_framework = """
--- 🏗️ CODE ARCHITECT REVIEW ---
1. 🔐 **SECURITY**: No hardcoded keys, no unsafe exec(user_input), robust validation.
2. 🚀 **PERFORMANCE**: Optimized loops, proper resource closing, efficient algorithms.
3. 🧹 **READABILITY**: PEP8 compliance, clear variable naming, modular structure.
4. 💡 **VERDICT**: [PASS/WARN/FAIL]
--- END REVIEW ---
"""
        return f"✅ Analysis Framework Loaded.\n{analysis_framework}\n\n[VICTOR]: Send me your code snippet and I will apply this framework immediately."
    except Exception as e:
        return f"❌ Code Review Error: {e}"

# --- TOOL 8: THE EYE (Vision) ---
def capture_screen() -> str:
    """Takes a high-resolution screenshot of the primary monitor."""
    try:
        workspace_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")
        if not os.path.exists(workspace_dir):
            os.makedirs(workspace_dir)
            
        screenshot_path = os.path.join(workspace_dir, "screen_capture.png")
        
        # Take the screenshot
        screenshot = pyautogui.screenshot()
        screenshot.save(screenshot_path)
        
        return os.path.abspath(screenshot_path)
    except Exception as e:
        return f"❌ Vision Error: {e}"

# --- TOOL 9: THE BACKUP (GitHub) ---
def push_to_github(commit_message: str) -> str:
    """Commits all changes and pushes to the configured GitHub repository."""
    try:
        import subprocess
        
        # 1. Check if git is initialized
        if not os.path.exists(".git"):
            subprocess.run(["git", "init"], check=True)
            
        # 2. Add all files
        subprocess.run(["git", "add", "."], check=True)
        
        # 3. Commit
        # Check if there are changes to commit first to avoid error
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            return "✅ No changes to commit."
            
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        
        # 4. Push
        # Check for remote
        remote = subprocess.run(["git", "remote", "-v"], capture_output=True, text=True)
        if "origin" not in remote.stdout:
            return "⚠️ Git committed locally, but NO REMOTE configured. Please set 'origin' using: git remote add origin <URL>"
            
        push_result = subprocess.run(["git", "push", "-u", "origin", "main"], capture_output=True, text=True) # Try main first
        if push_result.returncode != 0:
             # Fallback to master if main fails (common legacy issue)
             push_result = subprocess.run(["git", "push", "-u", "origin", "master"], capture_output=True, text=True)
             
        if push_result.returncode == 0:
            return "✅ Successfully pushed to GitHub!"
        else:
            return f"❌ Push failed: {push_result.stderr}"

    except Exception as e:
        return f"❌ Git Error: {e}"

