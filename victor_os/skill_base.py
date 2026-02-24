"""
Victor-OS Skill Base
Abstract base class and manifest for the plugin/skill system.
"""

from dataclasses import dataclass, field
from typing import Callable, Any
from abc import ABC, abstractmethod


@dataclass
class SkillManifest:
    """Metadata for a skill."""
    name: str                                           # Unique ID e.g. "price_tracker"
    display_name: str                                   # Human-readable e.g. "Price Tracker"
    version: str                                        # SemVer e.g. "1.0.0"
    description: str                                    # What this skill does
    triggers: list[str] = field(default_factory=list)   # Regex patterns that activate this skill
    schedule: str | None = None                         # e.g. "every 30 minutes"
    enabled: bool = True
    author: str = "system"
    required_env: tuple[str, ...] = field(default_factory=tuple)
    risk_profile: str = "medium"
    permissions_required: tuple[str, ...] = field(default_factory=tuple)
    tools_exposed: tuple[str, ...] = field(default_factory=tuple)
    config_schema: dict[str, Any] = field(default_factory=dict)


class Skill(ABC):
    """Base class for all Victor-OS skills."""

    @abstractmethod
    def manifest(self) -> SkillManifest:
        """Return the skill's metadata."""
        ...

    @abstractmethod
    def get_tools(self) -> list[Callable]:
        """Return a list of callable functions to register as ADK FunctionTools."""
        ...

    def get_agent_config(self) -> dict[str, Any] | None:
        """Optional: return agent configuration override (instruction, model, etc.)."""
        return None

    def on_load(self) -> None:
        """Called when skill is loaded. Use for initialization."""
        pass

    def on_unload(self) -> None:
        """Called when skill is unloaded. Use for cleanup."""
        pass

    def get_proactive_checks(self) -> list[dict[str, Any]]:
        """Return proactive check definitions for the scheduler.
        Each dict: {"name": str, "interval_seconds": int, "callback": Callable}
        """
        return []
