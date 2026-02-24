"""
Victor-OS Multi-Agent Router
=============================
Routes messages to specialist sub-agents based on intent tier, mode, and content signals.

Agents:
  chief_of_staff  — default, general-purpose (existing runner)
  research_agent  — deep research, web search, long-form answers
  code_agent      — code generation, review, debugging, technical precision
  ops_agent       — system health, task queue, ops commands
  comms_agent     — emails, messages, summaries, communications

Routing logic:
  1. Session mode override (@research, @code, @exec) → maps directly to agent
  2. Tier == COMPLEX → classify_intent() → route by intent_class
  3. Fallback → chief_of_staff

The router does NOT spawn separate ADK Runner instances — it selects a system
prompt prefix that re-orientates the Chief_of_Staff agent for the session.
For true agent isolation, specialist agents can be defined in agents.py
and the runner swapped per call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from local_inference_router import LocalInferenceRouter
from logging_config import get_logger

logger = get_logger("agent_router")

# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

@dataclass
class AgentSpec:
    agent_id: str
    display_name: str
    system_prompt_prefix: str
    skill_focus: list[str]       # hint — skills most relevant to this agent
    thinking_level: str = "standard"  # fast | standard | deep
    use_critic: bool = True


AGENT_REGISTRY: dict[str, AgentSpec] = {
    "chief_of_staff": AgentSpec(
        agent_id="chief_of_staff",
        display_name="Chief of Staff",
        system_prompt_prefix="",   # no prefix — default behaviour
        skill_focus=[],
        thinking_level="standard",
        use_critic=True,
    ),
    "research_agent": AgentSpec(
        agent_id="research_agent",
        display_name="Research Agent",
        system_prompt_prefix=(
            "[AGENT: RESEARCH]\n"
            "You are Victor's Research Officer. Your job is to go deep, not fast. "
            "Use web search tools. Cite sources. Organise findings into clear sections. "
            "Prioritise accuracy and completeness. If you are unsure, say so explicitly.\n\n"
        ),
        skill_focus=["research_agent", "knowledge_base", "daily_digest"],
        thinking_level="deep",
        use_critic=True,
    ),
    "code_agent": AgentSpec(
        agent_id="code_agent",
        display_name="Code Agent",
        system_prompt_prefix=(
            "[AGENT: CODE]\n"
            "You are Victor's Code Officer. Always include working code. "
            "Use language tags in all code blocks. Fix bugs if you see them. "
            "Explain only what is necessary. Prefer showing over telling.\n\n"
        ),
        skill_focus=["code_review", "self_coder", "document_brain"],
        thinking_level="standard",
        use_critic=True,
    ),
    "ops_agent": AgentSpec(
        agent_id="ops_agent",
        display_name="Ops Agent",
        system_prompt_prefix=(
            "[AGENT: OPS]\n"
            "You are Victor's Operations Officer. Focus on system health, task queues, "
            "and operational status. Be concise. Report numbers. Flag anomalies.\n\n"
        ),
        skill_focus=["ops_health_check", "market_watch", "goal_tracking", "task_delegation"],
        thinking_level="fast",
        use_critic=False,   # ops responses are factual, skip LLM scoring
    ),
    "comms_agent": AgentSpec(
        agent_id="comms_agent",
        display_name="Comms Agent",
        system_prompt_prefix=(
            "[AGENT: COMMS]\n"
            "You are Victor's Communications Officer. Draft messages, emails, and summaries "
            "with professional tone. Match the user's requested tone. Be concise and clear.\n\n"
        ),
        skill_focus=["email_intelligence", "unified_inbox", "daily_digest", "quick_capture"],
        thinking_level="standard",
        use_critic=True,
    ),
}

# ---------------------------------------------------------------------------
# Intent → Agent mapping (for COMPLEX tier when no mode override is set)
# ---------------------------------------------------------------------------

_INTENT_TO_AGENT: dict[str, str] = {
    "ops_status":          "ops_agent",
    "filesystem_routine":  "ops_agent",
    "invoice_routine":     "ops_agent",
    "research":            "research_agent",
    "code":                "code_agent",
    "comms":               "comms_agent",
    "novel":               "chief_of_staff",
}

# Mode → Agent mapping
_MODE_TO_AGENT: dict[str, str] = {
    "research": "research_agent",
    "code":     "code_agent",
    "exec":     "chief_of_staff",   # exec uses chief but with elevated prompt
    "default":  "chief_of_staff",
}

# Content signals: keywords that override intent classification
_CONTENT_SIGNALS: list[tuple[str, str]] = [
    # (regex_pattern, agent_id)
    (r"\b(write|draft|email|send|message|reply|slack)\b", "comms_agent"),
    (r"\b(code|debug|function|class|script|error|traceback|fix this|refactor)\b", "code_agent"),
    (r"\b(research|find out|look up|what is|who is|explain|history of|how does)\b", "research_agent"),
    (r"\b(health|status|queue|ops|monitor|cpu|memory|disk|task|invoice)\b", "ops_agent"),
]


class AgentRouter:
    """Routes each request to the most appropriate specialist agent."""

    def __init__(self) -> None:
        self._tier_router = LocalInferenceRouter()

    def route(
        self,
        user_text: str,
        session_mode: str = "default",
        tier: Optional[str] = None,
    ) -> AgentSpec:
        """
        Select the best agent for this request.

        Args:
            user_text:    The cleaned user message (directives already stripped).
            session_mode: Current session mode override (default/research/code/exec).
            tier:         Pre-computed tier (SOCIAL/SIMPLE/COMPLEX). If None, computed here.

        Returns:
            AgentSpec for the selected agent.
        """
        # 1. Session mode override takes highest priority
        if session_mode and session_mode != "default":
            agent_id = _MODE_TO_AGENT.get(session_mode, "chief_of_staff")
            spec = AGENT_REGISTRY[agent_id]
            logger.debug(f"AgentRouter: mode={session_mode} → {agent_id}")
            return spec

        # 2. Compute tier if not given
        if tier is None:
            tier = self._tier_router.classify_tier(user_text)

        # 3. SOCIAL/SIMPLE always use chief (fast path handles them anyway)
        if tier in ("SOCIAL", "SIMPLE"):
            return AGENT_REGISTRY["chief_of_staff"]

        # 4. Content signals (fast regex check before ML intent)
        import re
        t_lower = user_text.lower()
        for pattern, agent_id in _CONTENT_SIGNALS:
            if re.search(pattern, t_lower):
                spec = AGENT_REGISTRY.get(agent_id, AGENT_REGISTRY["chief_of_staff"])
                logger.debug(f"AgentRouter: content_signal → {agent_id}")
                return spec

        # 5. ML intent classification
        intent_class, confidence = self._tier_router.classify_intent(user_text)
        if confidence >= 0.65 and intent_class in _INTENT_TO_AGENT:
            agent_id = _INTENT_TO_AGENT[intent_class]
            spec = AGENT_REGISTRY.get(agent_id, AGENT_REGISTRY["chief_of_staff"])
            logger.debug(f"AgentRouter: intent={intent_class} conf={confidence:.2f} → {agent_id}")
            return spec

        # 6. Fallback
        logger.debug("AgentRouter: fallback → chief_of_staff")
        return AGENT_REGISTRY["chief_of_staff"]


# Module-level singleton
_router: AgentRouter | None = None


def get_agent_router() -> AgentRouter:
    global _router
    if _router is None:
        _router = AgentRouter()
    return _router
