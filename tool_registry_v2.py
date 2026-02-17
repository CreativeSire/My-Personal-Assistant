"""
Phase 2 Tool Registry v2
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ToolSpec:
    tool_name: str
    action: str
    input_schema: dict[str, Any]
    risk_class: str
    idempotency_strategy: str
    compensation: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolRegistryV2:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.tool_name] = spec

    def get(self, tool_name: str) -> ToolSpec | None:
        return self._tools.get(tool_name)

    def list_tools(self) -> list[dict[str, Any]]:
        return [spec.to_dict() for spec in sorted(self._tools.values(), key=lambda x: x.tool_name)]

    def seed_default_tools(self) -> None:
        defaults = [
            ToolSpec(
                tool_name="filesystem.list",
                action="read",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_class="low",
                idempotency_strategy="pure_read",
            ),
            ToolSpec(
                tool_name="filesystem.read",
                action="read",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_class="low",
                idempotency_strategy="pure_read",
            ),
            ToolSpec(
                tool_name="process.run_terminal",
                action="execute",
                input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
                risk_class="high",
                idempotency_strategy="dedupe_by_intent_hash",
                compensation="manual_rollback",
            ),
            ToolSpec(
                tool_name="messaging.telegram.send",
                action="send",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                risk_class="high",
                idempotency_strategy="ledger_dedupe_key",
            ),
            ToolSpec(
                tool_name="messaging.whatsapp.draft",
                action="write",
                input_schema={
                    "type": "object",
                    "properties": {"to": {"type": "string"}, "text": {"type": "string"}},
                },
                risk_class="medium",
                idempotency_strategy="draft_hash",
                compensation="delete_draft",
            ),
            ToolSpec(
                tool_name="messaging.whatsapp.send",
                action="send",
                input_schema={
                    "type": "object",
                    "properties": {"to": {"type": "string"}, "text": {"type": "string"}},
                },
                risk_class="high",
                idempotency_strategy="ledger_dedupe_key",
                compensation="followup_correction_message",
            ),
            ToolSpec(
                tool_name="invoice.batch_process",
                action="execute",
                input_schema={"type": "object", "properties": {"input_path": {"type": "string"}}},
                risk_class="medium",
                idempotency_strategy="idempotency_key",
            ),
        ]
        for spec in defaults:
            self.register(spec)
