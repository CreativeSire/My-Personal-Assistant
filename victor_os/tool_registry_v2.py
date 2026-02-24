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
                tool_name="filesystem.write_text",
                action="write",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "text": {"type": "string"}},
                },
                risk_class="medium",
                idempotency_strategy="content_hash",
                compensation="restore_previous_file_version",
            ),
            ToolSpec(
                tool_name="filesystem.move",
                action="write",
                input_schema={
                    "type": "object",
                    "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
                },
                risk_class="medium",
                idempotency_strategy="path_pair_hash",
                compensation="move_back",
            ),
            ToolSpec(
                tool_name="filesystem.copy",
                action="write",
                input_schema={
                    "type": "object",
                    "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
                },
                risk_class="low",
                idempotency_strategy="path_pair_hash",
                compensation="delete_copied_file",
            ),
            ToolSpec(
                tool_name="filesystem.delete",
                action="delete",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_class="high",
                idempotency_strategy="path_hash",
                compensation="restore_from_trash",
            ),
            ToolSpec(
                tool_name="filesystem.mkdir",
                action="write",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_class="low",
                idempotency_strategy="path_hash",
                compensation="delete_directory_if_empty",
            ),
            ToolSpec(
                tool_name="filesystem.glob",
                action="read",
                input_schema={
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}, "root": {"type": "string"}},
                },
                risk_class="low",
                idempotency_strategy="pure_read",
            ),
            ToolSpec(
                tool_name="browser.open_url",
                action="launch",
                input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
                risk_class="medium",
                idempotency_strategy="url_hash",
                compensation="close_tab",
            ),
            ToolSpec(
                tool_name="browser.read_dom_text",
                action="read",
                input_schema={
                    "type": "object",
                    "properties": {"url": {"type": "string"}, "selector": {"type": "string"}},
                },
                risk_class="low",
                idempotency_strategy="pure_read",
            ),
            ToolSpec(
                tool_name="browser.capture_screenshot",
                action="read",
                input_schema={
                    "type": "object",
                    "properties": {"url": {"type": "string"}, "path": {"type": "string"}},
                },
                risk_class="low",
                idempotency_strategy="time_window",
                compensation="delete_generated_file",
            ),
            ToolSpec(
                tool_name="browser.click_selector",
                action="execute",
                input_schema={
                    "type": "object",
                    "properties": {"url": {"type": "string"}, "selector": {"type": "string"}},
                },
                risk_class="high",
                idempotency_strategy="selector_hash",
                compensation="navigate_back",
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
                tool_name="process.list",
                action="read",
                input_schema={"type": "object", "properties": {}},
                risk_class="low",
                idempotency_strategy="pure_read",
            ),
            ToolSpec(
                tool_name="process.launch",
                action="launch",
                input_schema={"type": "object", "properties": {"app": {"type": "string"}}},
                risk_class="medium",
                idempotency_strategy="app_hash",
                compensation="terminate_launched_process",
            ),
            ToolSpec(
                tool_name="process.terminate",
                action="execute",
                input_schema={"type": "object", "properties": {"pid": {"type": "integer"}}},
                risk_class="high",
                idempotency_strategy="pid_hash",
                compensation="manual_restart_required",
            ),
            ToolSpec(
                tool_name="process.focus_window",
                action="execute",
                input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
                risk_class="medium",
                idempotency_strategy="title_hash",
                compensation="focus_previous_window",
            ),
            ToolSpec(
                tool_name="messaging.telegram.send",
                action="send",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                risk_class="high",
                idempotency_strategy="ledger_dedupe_key",
            ),
            ToolSpec(
                tool_name="messaging.telegram.send_file",
                action="send",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "caption": {"type": "string"}},
                },
                risk_class="high",
                idempotency_strategy="ledger_dedupe_key",
                compensation="send_followup_correction",
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
            ToolSpec(
                tool_name="invoice.get_artifacts",
                action="read",
                input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}},
                risk_class="low",
                idempotency_strategy="pure_read",
            ),
            ToolSpec(
                tool_name="vision.capture_screen",
                action="read",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_class="high",
                idempotency_strategy="time_window",
                compensation="delete_generated_file",
            ),
            ToolSpec(
                tool_name="vision.ocr_image",
                action="read",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                risk_class="medium",
                idempotency_strategy="pure_read",
            ),
            ToolSpec(
                tool_name="macro.run",
                action="execute",
                input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                risk_class="high",
                idempotency_strategy="dedupe_by_intent_hash",
                compensation="best_effort_rollback",
            ),
            ToolSpec(
                tool_name="style.apply",
                action="write",
                input_schema={
                    "type": "object",
                    "properties": {"contact": {"type": "string"}, "text": {"type": "string"}},
                },
                risk_class="low",
                idempotency_strategy="content_hash",
            ),
        ]
        for spec in defaults:
            self.register(spec)
