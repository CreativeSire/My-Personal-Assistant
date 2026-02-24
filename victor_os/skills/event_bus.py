"""
Victor-OS Skill: Event Bus
Internal publish/subscribe system for decoupling Victor components.
Skills and modules publish typed events; subscribers react asynchronously.
This is the foundation for reactive, event-driven automation inside Victor.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Any

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("event_bus_skill")


@dataclass
class BusEvent:
    event_type: str
    payload: dict[str, Any]
    publisher: str
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)


# Module-level singleton bus
_bus_instance: "EventBus | None" = None
_bus_lock = threading.Lock()


class EventBus:
    """
    Thread-safe publish/subscribe event bus.
    Subscribers register callbacks for event types (supports wildcards with '*').
    Events are dispatched asynchronously in a background thread pool.
    """

    def __init__(self):
        self._subscribers: dict[str, list[Callable[[BusEvent], None]]] = defaultdict(list)
        self._lock = threading.Lock()
        self._history: list[BusEvent] = []
        self._max_history = 500
        self._stats: dict[str, int] = defaultdict(int)

    def subscribe(self, event_type: str, callback: Callable[[BusEvent], None]) -> str:
        """
        Subscribe to events of a given type.
        Use '*' to receive all events.
        Returns a subscription ID for later unsubscription.
        """
        with self._lock:
            self._subscribers[event_type].append(callback)
        sub_id = f"{event_type}:{uuid.uuid4().hex[:8]}"
        logger.debug(f"EventBus: new subscriber for '{event_type}' ({sub_id})")
        return sub_id

    def unsubscribe_all(self, event_type: str) -> None:
        with self._lock:
            self._subscribers.pop(event_type, None)

    def publish(self, event_type: str, payload: dict, publisher: str = "unknown") -> BusEvent:
        """
        Publish an event to all subscribers of that type and wildcard subscribers.
        Dispatch is asynchronous (non-blocking).
        """
        event = BusEvent(event_type=event_type, payload=payload, publisher=publisher)
        with self._lock:
            # Keep rolling history
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history.pop(0)
            self._stats[event_type] += 1
            # Collect subscribers for this type + wildcard
            callbacks = (
                list(self._subscribers.get(event_type, []))
                + list(self._subscribers.get("*", []))
            )
        # Dispatch async to avoid blocking publisher
        for cb in callbacks:
            threading.Thread(target=self._safe_dispatch, args=(cb, event), daemon=True).start()
        return event

    @staticmethod
    def _safe_dispatch(callback: Callable, event: BusEvent) -> None:
        try:
            callback(event)
        except Exception as exc:
            logger.warning(f"EventBus subscriber error: {exc}")

    def get_recent_events(self, limit: int = 50, event_type: str | None = None) -> list[BusEvent]:
        with self._lock:
            events = list(self._history)
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events[-int(limit):]

    def get_stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)


def get_event_bus() -> EventBus:
    """Get or create the singleton EventBus."""
    global _bus_instance
    if _bus_instance is None:
        with _bus_lock:
            if _bus_instance is None:
                _bus_instance = EventBus()
    return _bus_instance


class EventBusSkill(Skill):
    """
    Exposes the internal EventBus as a Victor skill with tools for
    subscribing, publishing, and inspecting the event stream.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        self._bus = get_event_bus()
        # Bridge Victor data engine events → internal bus
        self._setup_bridge()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="event_bus",
            display_name="Event Bus",
            version="1.0.0",
            description="Internal publish/subscribe event bus for reactive automation.",
            triggers=[
                r"\bevent.*bus\b",
                r"\bpublish.*event\b",
                r"\bsubscrib\b",
                r"\bevent.*stream\b",
                r"\bautomation.*trigger\b",
                r"\bwhen.*event\b",
            ],
            enabled=True,
        )

    def _setup_bridge(self) -> None:
        """Subscribe to the data engine event stream and bridge to the bus."""
        try:
            # Subscribe to high-level Victor events via proactive engine hooks
            def _on_intent(event: BusEvent) -> None:
                self._de.emit_event(
                    self._de.build_event_envelope(
                        event.event_type,
                        session_id="event_bus",
                        task_id="",
                        actor=event.publisher,
                        payload=event.payload,
                        risk_score=0.1,
                        channel="system",
                        source="event_bus",
                    )
                )
            # Only bridge non-trivial events to avoid loops
            self._bus.subscribe("task.completed", _on_intent)
            self._bus.subscribe("goal.achieved", _on_intent)
            self._bus.subscribe("alert.critical", _on_intent)
        except Exception as exc:
            logger.debug(f"EventBus bridge setup warning: {exc}")

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_publish_event(self, event_type: str, payload_json: str = "{}") -> str:
        """
        Publish a custom event to the internal event bus.
        Args:
            event_type: Event type string (e.g. 'task.completed', 'alert.custom').
            payload_json: JSON string payload for the event.
        """
        event_type = str(event_type or "").strip()
        if not event_type:
            return "event_type is required."
        try:
            import json
            payload = json.loads(payload_json or "{}")
        except Exception:
            payload = {"raw": payload_json}
        event = self._bus.publish(event_type, payload, publisher="skill:event_bus")
        return f"Event published: {event.event_type} (id={event.event_id})"

    def tool_list_recent_events(self, limit: int = 20, event_type: str = "") -> str:
        """
        List recently published events on the internal bus.
        Args:
            limit: Max events to show.
            event_type: Optional filter by event type.
        """
        events = self._bus.get_recent_events(
            limit=int(limit),
            event_type=event_type.strip() or None,
        )
        if not events:
            return "No events on the bus yet."
        lines = [f"{len(events)} recent event(s):\n"]
        for e in reversed(events[-int(limit):]):
            ts = time.strftime("%H:%M:%S", time.localtime(e.ts))
            payload_preview = str(e.payload)[:80]
            lines.append(f"[{ts}] {e.event_type} from {e.publisher}: {payload_preview}")
        return "\n".join(lines)

    def tool_bus_stats(self) -> str:
        """Show event bus statistics: event type counts."""
        stats = self._bus.get_stats()
        if not stats:
            return "No events published yet."
        total = sum(stats.values())
        lines = [f"Event bus stats (total: {total} events):\n"]
        for etype, count in sorted(stats.items(), key=lambda x: -x[1]):
            lines.append(f"  {etype}: {count}")
        return "\n".join(lines)

    def tool_register_automation(self, event_type: str, action: str) -> str:
        """
        Register a simple automation: when event_type fires, log a specific action.
        This is the foundation for no-code event-driven automation rules.
        Args:
            event_type: The event type to listen for.
            action: Description of what to do (logged as a reminder for now).
        """
        if not event_type or not action:
            return "Both event_type and action are required."
        rule_id = uuid.uuid4().hex[:8]

        def _handler(event: BusEvent) -> None:
            logger.info(f"Automation rule {rule_id} triggered: {action} (event={event.event_type})")
            self._de.emit_event(
                self._de.build_event_envelope(
                    "automation.triggered",
                    session_id="event_bus",
                    task_id="",
                    actor="event_bus_automation",
                    payload={"rule_id": rule_id, "trigger": event_type, "action": action},
                    risk_score=0.2,
                    channel="system",
                    source="event_bus",
                )
            )

        self._bus.subscribe(event_type, _handler)
        return (
            f"Automation registered (id={rule_id}):\n"
            f"  When: {event_type}\n"
            f"  Action: {action}"
        )

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_publish_event,
            self.tool_list_recent_events,
            self.tool_bus_stats,
            self.tool_register_automation,
        ]


def create_skill():
    return EventBusSkill()
