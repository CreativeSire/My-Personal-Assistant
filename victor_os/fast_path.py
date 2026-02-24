"""
Fast-path handler for social/trivial messages.
No pipeline. No LLM. Instant response.
"""

import random
import re

GREETINGS = [
    "Hey - what are we doing today?",
    "Good to hear from you. What do you need?",
    "Victor here. What's the move?",
    "Ready when you are. What's on the agenda?",
    "Hey. What are we working on?",
]

THANKS = [
    "Of course.",
    "Anytime.",
    "Done.",
    "You got it.",
    "No problem.",
]

FAREWELLS = [
    "Talk later.",
    "Got it. I'll be here.",
    "Catch you next time.",
]

AFFIRMATIONS = [
    "Got it.",
    "Understood.",
    "Noted.",
    "Copy that.",
]


def get_fast_response(text: str) -> str:
    t = str(text or "").strip().lower()

    if re.search(r"\b(bye|goodbye|see you|later|cya|take care)\b", t):
        return random.choice(FAREWELLS)

    if re.search(r"\b(thanks|thank you|ty|cheers|appreciate it)\b", t):
        return random.choice(THANKS)

    if re.search(r"\b(got it|understood|noted|ok|okay|cool|great|perfect|sounds good|sure)\b", t):
        return random.choice(AFFIRMATIONS)

    if re.search(r"\bhow are you\b|what'?s up\b", t):
        return "Running well. What do you need?"

    # Default: treat as greeting
    return random.choice(GREETINGS)

