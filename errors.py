"""
Victor-OS Exception Hierarchy
Centralized error types for structured error handling across all modules.
"""


class VictorError(Exception):
    """Base exception for all Victor-OS errors."""

    def __init__(self, message: str, recoverable: bool = True):
        super().__init__(message)
        self.recoverable = recoverable


class APIError(VictorError):
    """External API call failed (Gemini, Telegram, Twilio)."""
    pass


class MemoryError(VictorError):
    """Memory vault read/write failure."""
    pass


class ToolError(VictorError):
    """Tool execution failure."""
    pass


class SessionError(VictorError):
    """Session service failure."""
    pass


class ChannelError(VictorError):
    """Messaging channel (Telegram/WhatsApp) failure."""
    pass


class SkillError(VictorError):
    """Skill loading or execution failure."""
    pass


class WorkflowError(VictorError):
    """Workflow definition or execution failure."""
    pass


class PlannerError(VictorError):
    """Plan decomposition or execution failure."""
    pass


class CriticError(VictorError):
    """Response evaluation failure."""
    pass


class GoalError(VictorError):
    """Goal tracking operation failure."""
    pass
