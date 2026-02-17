"""
Phase 2 end / Phase 3 bridge:
Local-vs-remote routing for routine decisions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RoutingDecision:
    provider: str  # local | remote
    confidence: float
    reason: str
    intent_class: str


class LocalInferenceRouter:
    def __init__(self, threshold: float = 0.7):
        self.threshold = float(threshold)

    def classify_intent(self, intent: str) -> tuple[str, float]:
        text = (intent or "").strip().lower()
        if any(k in text for k in ["status", "latest tasks", "task status", "health", "summary"]):
            return ("ops_status", 0.9)
        if any(k in text for k in ["list files", "show files", "read file", "open file"]):
            return ("filesystem_routine", 0.82)
        if any(k in text for k in ["invoice", "receipt", "batch"]):
            return ("invoice_routine", 0.74)
        return ("novel", 0.45)

    def route(self, intent: str) -> RoutingDecision:
        intent_class, confidence = self.classify_intent(intent)
        if confidence >= self.threshold and intent_class != "novel":
            return RoutingDecision(
                provider="local",
                confidence=confidence,
                reason="routine_intent_high_confidence",
                intent_class=intent_class,
            )
        return RoutingDecision(
            provider="remote",
            confidence=confidence,
            reason="novel_or_low_confidence",
            intent_class=intent_class,
        )
