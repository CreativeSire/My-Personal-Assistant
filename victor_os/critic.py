"""
Victor-OS Response Critic
Medium-aggressiveness quality gate: rule-based checks + single fast Gemini scoring call.
Fail-open design — if LLM scoring fails, response passes through unchanged.
"""

import json
import re
import ast
from dataclasses import dataclass, field

import google.genai as genai

from config import get_config
from logging_config import get_logger

logger = get_logger("critic")
cfg = get_config()


@dataclass
class CriticVerdict:
    """Result of evaluating a response."""
    approved: bool = True
    score: float = 10.0
    issues: list[str] = field(default_factory=list)
    revised_response: str | None = None
    rule_failures: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rule-based checks (instant, no API cost)
# ---------------------------------------------------------------------------

_PERSONA_BLOCKLIST = [
    "i am a large language model",
    "as a large language model",
    "i'm a large language model",
    "as an ai language model",
    "i am an ai",
    "as an ai,",
    "as an ai ",
    "i do not possess memory in the human sense",
    "i don't store information from previous conversations",
    "i do not have any persistent memory",
    "i don't retain information from past conversations",
    "i do not retain information about our past conversations",
    "i cannot access previous conversations",
    "i'm just a language model",
    "i'm just an ai",
    "a large language model",
    "i am programmed to be a helpful and harmless",
    "i am programmed to be helpful and harmless",
    "i apologize, but i encountered an issue processing your request",
    "i will notify the development team to investigate",
    "i am currently unable to answer your question",
    "what would you like me to help you with?",
]

_SENSITIVE_PATTERNS = [
    (r"[A-Za-z0-9+/]{30,}={0,2}", "potential base64 encoded secret"),
    (r"AIza[0-9A-Za-z\-_]{35}", "Google API key"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI-style API key"),
    (r"xox[boaprs]-[0-9a-zA-Z]{10,}", "Slack token"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "email address"),
]


def _check_persona(response: str) -> list[str]:
    """Check for persona-breaking phrases."""
    lowered = response.lower()
    failures = []
    for phrase in _PERSONA_BLOCKLIST:
        if phrase in lowered:
            failures.append(f"persona_break: '{phrase}'")
    return failures


def _check_completeness(user_input: str, response: str) -> list[str]:
    """Check that response is not empty or just tool/JSON traces."""
    failures = []
    stripped = response.strip()
    if not stripped:
        failures.append("empty_response")
        return failures
    if len(stripped) < 10 and len(user_input.strip()) > 20:
        failures.append("response_too_short")
    # Detect responses that are only JSON/tool output with no human text
    try:
        json.loads(stripped)
        # If the entire response is valid JSON, it's probably raw tool output
        if len(stripped) > 50:
            failures.append("raw_json_output: response appears to be unformatted tool output")
    except (json.JSONDecodeError, ValueError):
        pass
    return failures


def _check_code_validity(response: str) -> list[str]:
    """If response contains Python code blocks, do basic syntax check."""
    failures = []
    code_blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", response, re.DOTALL)
    for i, block in enumerate(code_blocks):
        block = block.strip()
        if not block:
            continue
        try:
            ast.parse(block)
        except SyntaxError as e:
            failures.append(f"code_syntax_error_block_{i + 1}: {e.msg} (line {e.lineno})")
    return failures


def _check_sensitive_data(response: str) -> list[str]:
    """Scan for potentially leaked secrets or PII."""
    failures = []
    for pattern, label in _SENSITIVE_PATTERNS:
        matches = re.findall(pattern, response)
        if matches and label != "email address":
            failures.append(f"sensitive_data: possible {label} detected")
        elif label == "email address" and len(matches) > 2:
            failures.append("sensitive_data: multiple email addresses exposed")
    return failures


def _check_coherence(user_input: str, response: str) -> list[str]:
    """Basic coherence heuristics."""
    failures = []
    # If user asked a question but response doesn't seem to address it
    if user_input.strip().endswith("?") and len(response.strip()) < 20:
        failures.append("possible_incomplete_answer: user asked a question, response is very short")
    # Detect excessive repetition
    words = response.lower().split()
    if len(words) > 50:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.2:
            failures.append("excessive_repetition: low unique word ratio")
    return failures


# ---------------------------------------------------------------------------
# LLM scoring (single fast Gemini call)
# ---------------------------------------------------------------------------

_SCORING_PROMPT = """\
You are a response quality evaluator. Score the following AI response on a scale of 0-10.

USER QUESTION:
{user_input}

AI RESPONSE:
{response}

Evaluate on these criteria:
1. Relevance: Does the response address the user's question/request?
2. Completeness: Is the answer thorough enough?
3. Accuracy: Are claims factual (to the best of your knowledge)?
4. Persona: Does the response maintain a professional executive assistant persona?
5. Clarity: Is the response well-organized and easy to understand?

Return ONLY a JSON object with this exact structure:
{{"score": <0-10>, "issues": ["<issue1>", "<issue2>"], "revised_response": "<improved version if score < {threshold}, otherwise null>"}}

RULES:
- If score >= {threshold}, set revised_response to null.
- If score < {threshold}, provide a revised_response that fixes the issues.
- The revised_response must maintain the Victor-OS executive persona.
- Keep issues list concise (max 3 items).
"""


def _llm_score(user_input: str, response: str, threshold: int) -> dict:
    """Single fast Gemini call to score the response. Returns dict with score, issues, revised_response."""
    try:
        api_key = cfg.gemini_api_key or cfg.google_api_key
        if not api_key:
            return {"score": 8.0, "issues": [], "revised_response": None}

        client = genai.Client(api_key=api_key)
        prompt = _SCORING_PROMPT.format(
            user_input=user_input[:500],
            response=response[:2000],
            threshold=threshold,
        )

        llm_response = client.models.generate_content(
            model=cfg.model_name,
            contents=prompt,
        )

        raw_text = ""
        if hasattr(llm_response, "text"):
            raw_text = llm_response.text
        elif hasattr(llm_response, "candidates") and llm_response.candidates:
            for candidate in llm_response.candidates:
                if hasattr(candidate, "content") and candidate.content:
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text:
                            raw_text += part.text

        raw_text = raw_text.strip()
        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```\s*$", "", raw_text)

        parsed = json.loads(raw_text)
        return {
            "score": float(parsed.get("score", 8.0)),
            "issues": parsed.get("issues", []),
            "revised_response": parsed.get("revised_response"),
        }
    except Exception as e:
        # Fail-open: if scoring fails, let the response through
        logger.warning(f"Critic LLM scoring failed (fail-open): {e}")
        return {"score": 8.0, "issues": [], "revised_response": None}


# ---------------------------------------------------------------------------
# Main evaluation interface
# ---------------------------------------------------------------------------

class ResponseCritic:
    """Medium-aggressiveness response quality gate."""

    def __init__(self):
        self._threshold = cfg.critic_score_threshold

    def evaluate(self, user_input: str, response: str) -> CriticVerdict:
        """Evaluate a response before delivery.

        Runs rule-based checks first (free + instant), then a single LLM scoring call.
        If rule checks find critical issues, skips LLM call and auto-revises.

        Args:
            user_input: The original user message.
            response: The generated response to evaluate.

        Returns:
            CriticVerdict with approval status, score, issues, and optional revision.
        """
        verdict = CriticVerdict()

        # --- Rule-based checks ---
        rule_failures = []
        rule_failures.extend(_check_persona(response))
        rule_failures.extend(_check_completeness(user_input, response))
        rule_failures.extend(_check_code_validity(response))
        rule_failures.extend(_check_sensitive_data(response))
        rule_failures.extend(_check_coherence(user_input, response))

        verdict.rule_failures = rule_failures

        # If persona break detected, auto-revise without LLM call
        persona_fails = [f for f in rule_failures if f.startswith("persona_break")]
        if persona_fails:
            verdict.approved = False
            verdict.score = 2.0
            verdict.issues = persona_fails
            verdict.revised_response = (
                "I maintain continuity using your active session context and long-term vault records. "
                "If you'd like, I can give you an executive sitrep of what I currently retain."
            )
            logger.info(f"Critic: persona break auto-revised, {len(persona_fails)} violations")
            return verdict

        # If empty response, flag it
        if "empty_response" in rule_failures:
            verdict.approved = False
            verdict.score = 0.0
            verdict.issues = ["empty_response"]
            verdict.revised_response = "I received your input, but couldn't generate a response. Let me try again."
            return verdict

        # --- LLM scoring ---
        llm_result = _llm_score(user_input, response, self._threshold)
        verdict.score = llm_result["score"]
        verdict.issues = rule_failures + (llm_result.get("issues") or [])

        if verdict.score < self._threshold:
            verdict.approved = False
            if llm_result.get("revised_response"):
                verdict.revised_response = llm_result["revised_response"]
            logger.info(f"Critic: score={verdict.score} below threshold={self._threshold}, issues={verdict.issues}")
        else:
            verdict.approved = True
            logger.debug(f"Critic: approved, score={verdict.score}")

        return verdict


# Module-level singleton
_critic: ResponseCritic | None = None


def get_critic() -> ResponseCritic:
    """Get or create the singleton ResponseCritic instance."""
    global _critic
    if _critic is None:
        _critic = ResponseCritic()
    return _critic
