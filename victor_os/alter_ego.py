"""
Project Alter Ego
Communication Style Mimicry Engine.
"""

import json
import os
from pathlib import Path
from google import genai
from google.genai import types
from victor_os.config import get_config

cfg = get_config()
STYLE_DB = Path("memory_store/style_profile.json")

def learn_style(sample_file: str):
    """
    Analyzes a text file of user messages to build a style profile.
    """
    if not os.path.exists(sample_file):
        print(f"Sample file not found: {sample_file}")
        return

    with open(sample_file, "r", encoding="utf-8") as f:
        samples = f.read()

    client = genai.Client(api_key=cfg.gemini_api_key)
    
    prompt = f"""
    Analyze the following messages sent by the user. 
    Extract their communication style, including:
    1. Tone (Formal, Casual, Blunt, Friendly)
    2. Length (Concise, Verbose)
    3. Formatting (Uses emojis? Lowercase? Punctuation?)
    4. Common phrases or quirks.

    MESSAGES:
    {samples[:5000]} # Limit context

    RESPONSE FORMAT (JSON):
    {{
        "tone": "...",
        "length": "...",
        "formatting": "...",
        "quirks": ["..."],
        "system_prompt_instruction": "You are impersonating the user. [Insert specific instructions here]"
    }}
    """

    response = client.models.generate_content(
        model=cfg.model_name,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    
    profile = json.loads(response.text)
    
    with open(STYLE_DB, "w") as f:
        json.dump(profile, f, indent=2)
    
    print("Style Profile Learned & Saved.")

def get_style_instruction() -> str:
    if not STYLE_DB.exists():
        return "You are a helpful assistant."
    
    with open(STYLE_DB, "r") as f:
        profile = json.load(f)
        return profile.get("system_prompt_instruction", "You are a helpful assistant.")

if __name__ == "__main__":
    # Create a dummy sample if none exists
    if not os.path.exists("my_messages.txt"):
        with open("my_messages.txt", "w") as f:
            f.write("Yeah sure, sounds good.
Ok on it.
Can you send that PDF?
thx")
    
    learn_style("my_messages.txt")
