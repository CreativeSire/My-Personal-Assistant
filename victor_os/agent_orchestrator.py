"""
Phase 2 Planner-Executor-Reviewer split.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from local_inference_router import LocalInferenceRouter
from tool_registry_v2 import ToolRegistryV2


@dataclass
class PlannedAction:
    tool_name: str
    inputs: dict[str, Any]
    risk_class: str


@dataclass
class PlanResult:
    intent: str
    provider: str
    confidence: float
    intent_class: str
    actions: list[PlannedAction]


@dataclass
class ExecutionResult:
    ok: bool
    action_results: list[dict[str, Any]]
    rollback_plan: list[dict[str, Any]]
    started_at: float
    completed_at: float


@dataclass
class ReviewResult:
    status: str  # approved | retry | rollback | ask_user
    score: float
    notes: str


class Planner:
    def __init__(self, registry: ToolRegistryV2, router: LocalInferenceRouter):
        self.registry = registry
        self.router = router

    def plan(self, intent: str, context: dict[str, Any] | None = None) -> PlanResult:
        context = context or {}
        decision = self.router.route(intent)
        text = (intent or "").lower()
        actions: list[PlannedAction] = []
        if "list" in text and "file" in text:
            spec = self.registry.get("filesystem.list")
            if spec:
                actions.append(
                    PlannedAction(
                        tool_name=spec.tool_name,
                        inputs={"path": context.get("path", ".")},
                        risk_class=spec.risk_class,
                    )
                )
        elif "status" in text:
            spec = self.registry.get("messaging.telegram.send")
            if spec:
                actions.append(
                    PlannedAction(
                        tool_name=spec.tool_name,
                        inputs={"text": "status_check"},
                        risk_class=spec.risk_class,
                    )
                )
        elif "whatsapp" in text and any(k in text for k in ["send", "text", "message"]):
            draft = self.registry.get("messaging.whatsapp.draft")
            send = self.registry.get("messaging.whatsapp.send")
            to = str(context.get("to") or "unknown")
            msg = str(context.get("text") or intent)
            if draft:
                actions.append(
                    PlannedAction(
                        tool_name=draft.tool_name,
                        inputs={"to": to, "text": msg},
                        risk_class=draft.risk_class,
                    )
                )
            if send:
                actions.append(
                    PlannedAction(
                        tool_name=send.tool_name,
                        inputs={"to": to, "text": msg},
                        risk_class=send.risk_class,
                    )
                )
        else:
            spec = self.registry.get("process.run_terminal")
            if spec:
                actions.append(
                    PlannedAction(
                        tool_name=spec.tool_name,
                        inputs={"command": "echo planned"},
                        risk_class=spec.risk_class,
                    )
                )
        return PlanResult(
            intent=intent,
            provider=decision.provider,
            confidence=decision.confidence,
            intent_class=decision.intent_class,
            actions=actions,
        )


class Executor:
    def __init__(self, executors: dict[str, Callable[[dict[str, Any]], Any]]):
        self.executors = executors

    def execute(self, plan: PlanResult) -> ExecutionResult:
        started = time.time()
        action_results: list[dict[str, Any]] = []
        rollback_plan: list[dict[str, Any]] = []
        ok = True
        for action in plan.actions:
            handler = self.executors.get(action.tool_name)
            if handler is None:
                ok = False
                action_results.append(
                    {"tool_name": action.tool_name, "ok": False, "error": "handler_not_found"}
                )
                continue
            try:
                output = handler(action.inputs)
                action_results.append({"tool_name": action.tool_name, "ok": True, "output": output})
                rollback_plan.append(
                    {
                        "tool_name": action.tool_name,
                        "strategy": "best_effort_compensation",
                        "inputs": action.inputs,
                    }
                )
            except Exception as exc:
                ok = False
                action_results.append({"tool_name": action.tool_name, "ok": False, "error": str(exc)})
        return ExecutionResult(
            ok=ok,
            action_results=action_results,
            rollback_plan=rollback_plan,
            started_at=started,
            completed_at=time.time(),
        )


class Reviewer:
    def review(self, plan: PlanResult, execution: ExecutionResult) -> ReviewResult:
        if execution.ok:
            return ReviewResult(status="approved", score=0.9, notes="all_actions_succeeded")
        if plan.provider == "local":
            return ReviewResult(status="rollback", score=0.45, notes="local_path_failure_rollback_then_retry_remote")
        return ReviewResult(status="ask_user", score=0.2, notes="remote_execution_failure")


class PlannerExecutorReviewer:
    def __init__(self, planner: Planner, executor: Executor, reviewer: Reviewer):
        self.planner = planner
        self.executor = executor
        self.reviewer = reviewer

    def run(self, intent: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = self.planner.plan(intent, context)
        execution = self.executor.execute(plan)
        review = self.reviewer.review(plan, execution)
        return {
            "plan": {
                "intent": plan.intent,
                "provider": plan.provider,
                "confidence": plan.confidence,
                "intent_class": plan.intent_class,
                "actions": [
                    {"tool_name": a.tool_name, "inputs": a.inputs, "risk_class": a.risk_class}
                    for a in plan.actions
                ],
            },
            "execution": {
                "ok": execution.ok,
                "started_at": execution.started_at,
                "completed_at": execution.completed_at,
                "actions": execution.action_results,
                "rollback_plan": execution.rollback_plan,
            },
            "review": {"status": review.status, "score": review.score, "notes": review.notes},
        }
