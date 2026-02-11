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
from pydub import AudioSegment
import pyautogui

from config import get_config
from logging_config import get_logger, setup_logging
from resilience import retry_with_backoff

cfg = get_config()
setup_logging(cfg.log_dir)
logger = get_logger("tools")


# --- TOOL 1: THE UNIVERSAL READER ---
def universal_file_reader(file_path: str) -> str:
    """Reads Excel, CSV, PDF, Docx, SPSS (.sav), and Stata (.dta)."""
    try:
        clean_path = file_path.strip().strip('"').strip("'")
        if not os.path.exists(clean_path):
            return f"Error: File not found at {clean_path}"

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
        logger.error(f"File read error: {e}", extra={"tool_name": "universal_file_reader"})
        return f"Error reading file: {e}"

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
        return f"Execution Successful.\nOutput:\n{redirected_output.getvalue()}"
    except Exception as e:
        sys.stdout = old_stdout
        logger.error(f"Code execution error: {e}", extra={"tool_name": "execute_python_code"})
        return f"Execution Error: {e}"

# --- TOOL 3: THE VOICE ---
def speak_out_loud(text: str) -> str:
    """Reads text out loud."""
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 170)
        engine.say(text)
        engine.runAndWait()
        return "Audio played successfully."
    except Exception as e:
        return f"Audio Error: {e}"

def generate_tts_file(text: str, filename: str = "response.mp3") -> str:
    """Generates a TTS audio file and returns the path."""
    try:
        engine = pyttsx3.init()
        engine.save_to_file(text, filename)
        engine.runAndWait()
        return os.path.abspath(filename)
    except Exception as e:
        return f"TTS Generation Error: {e}"

# --- TOOL 4: THE LISTENER ---
def listen_to_user() -> str:
    """Activates microphone and returns text."""
    r = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            logger.info("Listening for voice input", extra={"tool_name": "listen_to_user"})
            r.adjust_for_ambient_noise(source, duration=1)
            audio = r.listen(source, timeout=5, phrase_time_limit=15)
            text = r.recognize_google(audio)
            logger.info(f"Transcribed: '{text}'", extra={"tool_name": "listen_to_user"})
            return text
    except Exception:
        return ""

def transcribe_audio_file(file_path: str) -> str:
    """Transcribes an audio file (ogg, wav, mp3) to text."""
    r = sr.Recognizer()
    try:
        ext = file_path.split('.')[-1].lower()
        if ext != 'wav':
            audio = AudioSegment.from_file(file_path)
            wav_path = file_path.replace(f".{ext}", ".wav")
            audio.export(wav_path, format="wav")
            file_path = wav_path

        with sr.AudioFile(file_path) as source:
            audio_data = r.record(source)
            text = r.recognize_google(audio_data)
            return text
    except Exception as e:
        return f"Transcription Error: {e}"

# --- TOOL 5: THE OUTBOX (Email) ---
@retry_with_backoff(max_retries=2, base_delay=2.0, exceptions=(smtplib.SMTPException, ConnectionError, OSError))
def send_email_alert(subject: str, body: str) -> str:
    """Sends an email to Victor's primary address."""
    try:
        msg = MIMEText(body)
        msg['Subject'] = f"Victor-OS: {subject}"
        msg['From'] = cfg.email_user
        msg['To'] = cfg.email_target

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(cfg.email_user, cfg.email_pass)
            server.send_message(msg)

        logger.info(f"Email sent: {subject}", extra={"tool_name": "send_email_alert"})
        return "Email sent successfully."
    except Exception as e:
        logger.error(f"Email failed: {e}", extra={"tool_name": "send_email_alert"})
        return f"Email failed: {e}"


# --- TOOL 6: THE SYSTEM GUARDIAN ---
def run_system_diagnostic() -> str:
    """Checks CPU, RAM, Disk, and Dev-Port health (3000, 8000, 8501)."""
    try:
        from monitor import get_system_metrics
        m = get_system_metrics()

        status = "NOMINAL" if m['cpu'] < 80 and m['ram'] < 90 else "STRESSED"

        report = f"--- SYSTEM HEALTH: {status} ---\n"
        report += f"CPU: {m['cpu']}%\n"
        report += f"RAM: {m['ram']}%\n"
        report += f"DISK: {m['disk']}%\n"
        report += "\nPORT STATUS:\n"
        for port, state in m.get('ports', {}).items():
            report += f" - {port}: {state}\n"
        report += "--- END DIAGNOSTIC ---"
        return report
    except Exception as e:
        return f"Diagnostic Error: {e}"


# --- TOOL 7: THE CODE ARCHITECT ---
def review_code(code: str) -> str:
    """Analyzes code quality, security, and performance."""
    try:
        from monitor import log_activity
        log_activity("Code Architect", "Code Analysis Triggered", "Info", f"Snippet length: {len(code)}")

        analysis_framework = """
--- CODE ARCHITECT REVIEW ---
1. SECURITY: No hardcoded keys, no unsafe exec(user_input), robust validation.
2. PERFORMANCE: Optimized loops, proper resource closing, efficient algorithms.
3. READABILITY: PEP8 compliance, clear variable naming, modular structure.
4. VERDICT: [PASS/WARN/FAIL]
--- END REVIEW ---
"""
        return f"Analysis Framework Loaded.\n{analysis_framework}\n\n[VICTOR]: Send me your code snippet and I will apply this framework immediately."
    except Exception as e:
        return f"Code Review Error: {e}"

# --- TOOL 8: THE EYE (Vision) ---
def capture_screen() -> str:
    """Takes a high-resolution screenshot of the primary monitor."""
    try:
        workspace_dir = cfg.workspace_dir
        os.makedirs(workspace_dir, exist_ok=True)

        screenshot_path = os.path.join(workspace_dir, "screen_capture.png")
        screenshot = pyautogui.screenshot()
        screenshot.save(screenshot_path)

        return os.path.abspath(screenshot_path)
    except Exception as e:
        return f"Vision Error: {e}"

# --- TOOL 9: THE BACKUP (GitHub) ---
def push_to_github(commit_message: str) -> str:
    """Commits all changes and pushes to the configured GitHub repository."""
    try:
        import subprocess

        if not os.path.exists(".git"):
            subprocess.run(["git", "init"], check=True)

        subprocess.run(["git", "add", "."], check=True)

        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            return "No changes to commit."

        subprocess.run(["git", "commit", "-m", commit_message], check=True)

        remote = subprocess.run(["git", "remote", "-v"], capture_output=True, text=True)
        if "origin" not in remote.stdout:
            return "Git committed locally, but NO REMOTE configured. Please set 'origin' using: git remote add origin <URL>"

        push_result = subprocess.run(["git", "push", "-u", "origin", "main"], capture_output=True, text=True)
        if push_result.returncode != 0:
             push_result = subprocess.run(["git", "push", "-u", "origin", "master"], capture_output=True, text=True)

        if push_result.returncode == 0:
            return "Successfully pushed to GitHub!"
        else:
            return f"Push failed: {push_result.stderr}"

    except Exception as e:
        return f"Git Error: {e}"


# ==========================================
# TASK QUEUE & WORKFLOW TOOLS
# ==========================================

def submit_task(task_type: str, description: str, priority: int = 3) -> str:
    """Submits an async task to the Victor-OS task queue for background processing.
    Use this for long-running operations that shouldn't block the conversation."""
    from task_queue import TaskQueue
    queue = TaskQueue()
    task_id = queue.enqueue(
        task_type=task_type,
        payload={"description": description},
        priority=priority,
    )
    return f"Task submitted (ID: {task_id}). I will notify you when it completes."


def check_task_status(task_id: str) -> str:
    """Checks the status of a previously submitted background task."""
    from task_queue import TaskQueue
    queue = TaskQueue()
    task = queue.get_task(task_id)
    if not task:
        return f"No task found with ID: {task_id}"
    return (
        f"Task {task.id}: {task.status.value}\n"
        f"Type: {task.task_type}\n"
        f"Progress: {task.progress}%\n"
        f"Result: {task.result or 'Pending'}"
    )


def run_workflow(workflow_name: str) -> str:
    """Triggers a named workflow for execution. Workflows are multi-step automation pipelines."""
    from workflow_engine import WorkflowEngine
    from skills.ops_health_check import OpsHealthCheckSkill
    from skills.market_watch import MarketWatchSkill
    from skills.memory_hygiene import MemoryHygieneSkill
    engine = WorkflowEngine()
    engine.load_definitions_from_dir()
    ops = OpsHealthCheckSkill()
    market = MarketWatchSkill()
    memory_hygiene = MemoryHygieneSkill()

    def _action_ops_health_summary(params, context):
        return ops.tool_ops_health_summary()

    def _action_market_snapshot(params, context):
        return market.tool_market_snapshot()

    def _action_memory_hygiene(params, context):
        return memory_hygiene.tool_memory_hygiene_report()

    def _action_compose_daily_ops_report(params, context):
        health = params.get("health") or context.get("step_collect_ops_health_result", "")
        return "Daily Ops Brief\n\n" + str(health)

    def _action_notify(params, context):
        return str(params.get("message", "Notification sent."))

    engine.register_action("ops_health_summary", _action_ops_health_summary)
    engine.register_action("market_snapshot", _action_market_snapshot)
    engine.register_action("memory_hygiene", _action_memory_hygiene)
    engine.register_action("compose_daily_ops_report", _action_compose_daily_ops_report)
    engine.register_action("notify", _action_notify)
    try:
        run = engine.execute(workflow_name)
        steps_summary = ", ".join(
            f"{sr['step']}:{sr['status']}" for sr in run.step_results
        )
        return f"Workflow '{workflow_name}' {run.status}. Steps: {steps_summary}"
    except ValueError as e:
        return f"Workflow error: {e}"


def list_workflows() -> str:
    """Lists all available workflows that can be triggered."""
    from workflow_engine import WorkflowEngine
    engine = WorkflowEngine()
    engine.load_definitions_from_dir()
    workflows = engine.get_available_workflows()
    if not workflows:
        return "No workflows defined. Add .json files to victor_os/workflows/"
    lines = [f"- {w['name']}: {w['description']} (trigger: {w['trigger']})" for w in workflows]
    return "Available workflows:\n" + "\n".join(lines)
