"""
Victor-OS Workflow Engine
JSON-defined multi-step workflow executor with conditional branching.
"""

import ast
import os
import json
import re
import threading
import time
import uuid
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any, Callable

import schedule as schedule_lib

from config import get_config
from logging_config import get_logger
from data_engine import get_data_engine

logger = get_logger("workflow_engine")


class _SafeConditionEvaluator(ast.NodeVisitor):
    """Evaluates simple boolean conditions against a context dict without using eval()."""

    _SAFE_OPS = {
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
        ast.Is: lambda a, b: a is b,
        ast.IsNot: lambda a, b: a is not b,
    }

    def __init__(self, context: dict[str, Any]):
        self._ctx = context

    def evaluate(self, expression: str) -> bool:
        try:
            tree = ast.parse(expression, mode="eval")
            result = self._visit(tree.body)
            return bool(result)
        except Exception:
            return False

    def _visit(self, node: ast.expr) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self._ctx.get(node.id)
        if isinstance(node, ast.Attribute):
            obj = self._visit(node.value)
            return getattr(obj, node.attr, None)
        if isinstance(node, ast.Subscript):
            obj = self._visit(node.value)
            key = self._visit(node.slice)
            try:
                return obj[key]  # type: ignore[index]
            except Exception:
                return None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not self._visit(node.operand)
        if isinstance(node, ast.BoolOp):
            values = [self._visit(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            return any(values)
        if isinstance(node, ast.Compare):
            left = self._visit(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._visit(comparator)
                op_fn = self._SAFE_OPS.get(type(op))
                if op_fn is None:
                    return False
                if not op_fn(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.BinOp):
            left = self._visit(node.left)
            right = self._visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right  # type: ignore[operator]
            if isinstance(node.op, ast.Sub):
                return left - right  # type: ignore[operator]
        # Unsupported node type — fail safe
        return False


def _safe_eval_condition(expression: str, context: dict[str, Any]) -> bool:
    """Safely evaluate a workflow step condition without using eval()."""
    return _SafeConditionEvaluator(context).evaluate(expression)


@dataclass
class WorkflowStep:
    name: str
    action: str                          # action handler name
    params: dict[str, Any] = field(default_factory=dict)
    on_success: str | None = None        # next step name (default: sequential)
    on_failure: str | None = None        # step name on failure, or "abort"
    condition: str | None = None         # Python expression evaluated against context
    timeout_seconds: int = 300
    retries: int = 0


@dataclass
class WorkflowDefinition:
    name: str
    description: str
    steps: list[WorkflowStep]
    trigger: str | None = None           # "schedule:08:00" or "event:keyword" or "manual"
    version: str = "1.0.0"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRun:
    workflow_name: str
    run_id: str
    status: str                          # "running", "completed", "failed"
    current_step: int
    context: dict[str, Any]              # Accumulates results from each step
    step_results: list[dict[str, Any]]
    started_at: float
    dedupe_token: str = ""
    completed_at: float | None = None


class WorkflowEngine:
    """Executes multi-step workflows defined as JSON configs."""

    def __init__(self):
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._action_handlers: dict[str, Callable] = {}
        self._action_meta: dict[str, dict[str, Any]] = {}
        self._workflows_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "workflows"
        )
        self._run_locks: dict[str, threading.Lock] = {}
        self._active_tokens: set[str] = set()
        self._runtime_lock = threading.RLock()
        self._cfg = get_config()
        self._state_path = os.path.join(self._cfg.base_dir, "memory_store", "workflow_runtime_state.json")
        self._engine = get_data_engine()

    def register_action(
        self,
        name: str,
        handler: Callable[[dict[str, Any], dict[str, Any]], str],
        *,
        idempotency_class: str = "idempotent",
    ) -> None:
        """Register an action handler. Signature: handler(params, context) -> result_string."""
        self._action_handlers[name] = handler
        cls = str(idempotency_class or "idempotent").strip().lower()
        if cls not in {"idempotent", "safe_side_effect", "non_idempotent"}:
            cls = "idempotent"
        self._action_meta[name] = {"idempotency_class": cls}

    def load_definitions_from_dir(self) -> int:
        """Load all .json workflow definitions from workflows/ directory."""
        os.makedirs(self._workflows_dir, exist_ok=True)
        count = 0
        for filename in os.listdir(self._workflows_dir):
            if filename.endswith(".json"):
                filepath = os.path.join(self._workflows_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    wf = self._parse_definition(data)
                    self._definitions[wf.name] = wf
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to load workflow '{filename}': {e}")
        logger.info(f"Loaded {count} workflow definition(s)")
        return count

    def _parse_definition(self, data: dict) -> WorkflowDefinition:
        steps = []
        for step_data in data.get("steps", []):
            steps.append(WorkflowStep(
                name=step_data["name"],
                action=step_data["action"],
                params=step_data.get("params", {}),
                on_success=step_data.get("on_success"),
                on_failure=step_data.get("on_failure"),
                condition=step_data.get("condition"),
                timeout_seconds=step_data.get("timeout_seconds", 300),
                retries=step_data.get("retries", 0),
            ))
        return WorkflowDefinition(
            name=data["name"],
            description=data.get("description", ""),
            steps=steps,
            trigger=data.get("trigger"),
            version=data.get("version", "1.0.0"),
        )

    def _state_load(self) -> dict[str, Any]:
        if not os.path.exists(self._state_path):
            return {"last_runs": {}, "recent_tokens": []}
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"last_runs": {}, "recent_tokens": []}
            data.setdefault("last_runs", {})
            data.setdefault("recent_tokens", [])
            return data
        except Exception:
            return {"last_runs": {}, "recent_tokens": []}

    def _state_save(self, state: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _record_last_run(self, workflow_name: str, ts: float, dedupe_token: str) -> None:
        with self._runtime_lock:
            state = self._state_load()
            state["last_runs"][workflow_name] = float(ts)
            recent = list(state.get("recent_tokens") or [])
            recent.append({"token": dedupe_token, "ts": float(ts), "workflow": workflow_name})
            state["recent_tokens"] = recent[-500:]
            self._state_save(state)

    def _seen_token(self, dedupe_token: str) -> bool:
        with self._runtime_lock:
            state = self._state_load()
            for row in state.get("recent_tokens", []):
                if str(row.get("token") or "") == dedupe_token:
                    return True
            return False

    def _action_idempotency(self, action_name: str) -> str:
        meta = self._action_meta.get(action_name) or {}
        cls = str(meta.get("idempotency_class") or "idempotent")
        return cls if cls in {"idempotent", "safe_side_effect", "non_idempotent"} else "idempotent"

    def _retry_delay_for(self, idempotency_class: str, attempt_idx: int) -> float:
        # attempt_idx starts at 0.
        matrix = {
            "idempotent": [0.5, 1.0, 2.0, 4.0],
            "safe_side_effect": [1.0, 2.0, 4.0, 8.0],
            "non_idempotent": [2.0, 5.0, 10.0, 15.0],
        }
        seq = matrix.get(idempotency_class, matrix["idempotent"])
        return float(seq[min(max(attempt_idx, 0), len(seq) - 1)])

    def _is_side_effect_action(self, action_name: str) -> bool:
        a = str(action_name or "").lower()
        return (
            "notify" in a
            or "send" in a
            or "email" in a
            or self._action_idempotency(action_name) in {"safe_side_effect", "non_idempotent"}
        )

    def execute(
        self,
        workflow_name: str,
        initial_context: dict[str, Any] | None = None,
        *,
        dedupe_token: str | None = None,
    ) -> WorkflowRun:
        """Execute a workflow synchronously. Returns the completed WorkflowRun."""
        wf = self._definitions.get(workflow_name)
        if not wf:
            raise ValueError(f"Unknown workflow: {workflow_name}")
        wf_lock = self._run_locks.setdefault(workflow_name, threading.Lock())
        if not wf_lock.acquire(blocking=False):
            raise RuntimeError(f"Workflow '{workflow_name}' is already running")

        token = str(dedupe_token or "").strip()
        if not token:
            token = hashlib.sha256(f"{workflow_name}|{time.time()}|{uuid.uuid4().hex}".encode("utf-8")).hexdigest()[:24]
        if token in self._active_tokens or self._seen_token(token):
            wf_lock.release()
            raise RuntimeError(f"Duplicate workflow run token detected: {token}")
        self._active_tokens.add(token)

        run = WorkflowRun(
            workflow_name=workflow_name,
            run_id=uuid.uuid4().hex[:12],
            status="running",
            current_step=0,
            context=initial_context or {},
            step_results=[],
            started_at=time.time(),
            dedupe_token=token,
        )

        logger.info(f"Starting workflow '{workflow_name}' run={run.run_id}")

        try:
            step_index = 0
            steps_by_name = {s.name: i for i, s in enumerate(wf.steps)}

            while step_index < len(wf.steps):
                step = wf.steps[step_index]
                run.current_step = step_index

                # Evaluate condition (safe AST evaluator — no exec/eval)
                if step.condition:
                    try:
                        if not _safe_eval_condition(step.condition, run.context):
                            logger.info(f"Step '{step.name}' skipped (condition false)")
                            run.step_results.append({"step": step.name, "status": "skipped"})
                            step_index += 1
                            continue
                    except Exception as e:
                        logger.warning(f"Condition eval failed for '{step.name}': {e}")

                # Execute action
                handler = self._action_handlers.get(step.action)
                if not handler:
                    error = f"No handler for action '{step.action}'"
                    logger.error(error)
                    run.step_results.append({"step": step.name, "status": "failed", "error": error})
                    if step.on_failure and step.on_failure != "abort":
                        step_index = steps_by_name.get(step.on_failure, len(wf.steps))
                        continue
                    run.status = "failed"
                    break

                idempotency_class = self._action_idempotency(step.action)

                # Resolve $ctx. references in params
                resolved_params = {}
                for k, v in step.params.items():
                    if isinstance(v, str) and v.startswith("$ctx."):
                        ctx_key = v[5:]
                        resolved_params[k] = run.context.get(ctx_key, v)
                    else:
                        resolved_params[k] = v

                side_effect_guard_key = ""
                if self._is_side_effect_action(step.action):
                    side_effect_guard_key = f"{workflow_name}:{run.dedupe_token}:{step.name}:{hashlib.sha256(json.dumps(resolved_params, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:12]}"
                    if self._engine.has_delivery(
                        destination=f"workflow:{workflow_name}",
                        dedupe_key=side_effect_guard_key,
                        payload=resolved_params,
                    ):
                        run.step_results.append({"step": step.name, "status": "skipped_duplicate", "result": "guarded_by_dedupe"})
                        run.context[f"step_{step.name}_result"] = "guarded_by_dedupe"
                        step_index += 1
                        continue

                attempts = step.retries + 1
                success = False
                result = ""
                for attempt in range(attempts):
                    try:
                        if attempt > 0:
                            time.sleep(self._retry_delay_for(idempotency_class, attempt - 1))
                        result = handler(resolved_params, run.context)
                        success = True
                        break
                    except Exception as e:
                        logger.warning(f"Step '{step.name}' attempt {attempt + 1} failed: {e}")
                        if attempt == attempts - 1:
                            result = str(e)

                if success and side_effect_guard_key:
                    self._engine.record_delivery_attempt(
                        destination=f"workflow:{workflow_name}",
                        dedupe_key=side_effect_guard_key,
                        payload=resolved_params,
                        status="sent",
                        transport="workflow_engine",
                        task_id=run.run_id,
                        metadata={"step": step.name, "run_id": run.run_id},
                    )

                step_result = {
                    "step": step.name,
                    "status": "completed" if success else "failed",
                    "result": result,
                    "idempotency_class": idempotency_class,
                }
                run.step_results.append(step_result)
                run.context[f"step_{step.name}_result"] = result

                if success:
                    if step.on_success and step.on_success in steps_by_name:
                        step_index = steps_by_name[step.on_success]
                    else:
                        step_index += 1
                else:
                    if step.on_failure and step.on_failure != "abort" and step.on_failure in steps_by_name:
                        step_index = steps_by_name[step.on_failure]
                    else:
                        run.status = "failed"
                        break

            if run.status == "running":
                run.status = "completed"
            run.completed_at = time.time()
            self._record_last_run(workflow_name, run.completed_at, run.dedupe_token)
            logger.info(f"Workflow '{workflow_name}' run={run.run_id} finished: {run.status}")
            return run
        finally:
            self._active_tokens.discard(token)
            wf_lock.release()

    def get_available_workflows(self) -> list[dict[str, str]]:
        return [
            {"name": wf.name, "description": wf.description, "trigger": wf.trigger or "manual"}
            for wf in self._definitions.values()
        ]

    def enqueue_workflow_run(
        self,
        queue,
        workflow_name: str,
        initial_context: dict[str, Any] | None = None,
        channel: str = "system",
        user_id: str | None = None,
        priority: int = 3,
    ) -> str:
        """Queue-backed workflow execution adapter for TaskQueue integration."""
        payload = {
            "workflow_name": workflow_name,
            "initial_context": initial_context or {},
        }
        return queue.enqueue(
            task_type="workflow_run",
            payload=payload,
            channel=channel,
            user_id=user_id,
            priority=priority,
        )

    def execute_with_notifications(
        self,
        workflow_name: str,
        initial_context: dict[str, Any] | None = None,
        notify: Callable[[str], None] | None = None,
    ) -> WorkflowRun:
        run = self.execute(workflow_name, initial_context=initial_context)
        if notify:
            try:
                if run.status == "completed":
                    notify(f"Workflow '{workflow_name}' completed successfully.")
                else:
                    notify(f"Workflow '{workflow_name}' failed.")
            except Exception as e:
                logger.warning(f"Workflow notification failed: {e}")
        return run

    def get_scheduled_workflows(self) -> list[tuple[str, str]]:
        """Return (workflow_name, schedule_time) for workflows with schedule triggers."""
        results = []
        for wf in self._definitions.values():
            trigger = wf.trigger or ""
            # Parse "schedule:HH:MM" or "schedule:dayofweek:HH:MM"
            if trigger.startswith("schedule:"):
                results.append((wf.name, trigger))
        return results


class WorkflowScheduler:
    """Background thread that runs scheduled workflows based on their trigger fields.

    Parses trigger formats:
      - "schedule:08:00"           → every day at 08:00
      - "schedule:monday:09:00"    → every monday at 09:00
      - "schedule:sunday:18:00"    → every sunday at 18:00
    """

    def __init__(self, engine: WorkflowEngine, notify_callback: Callable[[str, str, str], None] | None = None):
        self._engine = engine
        self._notify = notify_callback
        self._cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._scheduler = schedule_lib.Scheduler()

    def _parse_and_register(self):
        """Parse trigger fields from all workflows and register with schedule."""
        scheduled = self._engine.get_scheduled_workflows()
        for wf_name, trigger in scheduled:
            parts = trigger.split(":")
            # "schedule:HH:MM" → parts = ["schedule", "HH", "MM"]
            # "schedule:monday:HH:MM" → parts = ["schedule", "monday", "HH", "MM"]
            if len(parts) == 3:
                # Daily at HH:MM
                time_str = f"{parts[1]}:{parts[2]}"
                self._scheduler.every().day.at(time_str).do(self._run_workflow, wf_name)
                logger.info(f"Scheduled workflow '{wf_name}' daily at {time_str}")
            elif len(parts) == 4:
                # Specific day at HH:MM
                day = parts[1].lower()
                time_str = f"{parts[2]}:{parts[3]}"
                day_job = getattr(self._scheduler.every(), day, None)
                if day_job:
                    day_job.at(time_str).do(self._run_workflow, wf_name)
                    logger.info(f"Scheduled workflow '{wf_name}' every {day} at {time_str}")
                else:
                    logger.warning(f"Unknown day '{day}' in trigger for workflow '{wf_name}'")

    def _should_catch_up(self, trigger: str, last_run_ts: float) -> tuple[bool, str]:
        """
        Return (should_run_now, slot_token).
        Catch-up executes at most one missed slot at startup.
        """
        now = datetime.now()
        parts = trigger.split(":")
        if len(parts) not in {3, 4}:
            return False, ""
        if len(parts) == 3:
            hh, mm = int(parts[1]), int(parts[2])
            slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now < slot:
                return False, ""
            should = last_run_ts <= (slot - timedelta(seconds=1)).timestamp()
            token = f"daily:{slot.strftime('%Y%m%d%H%M')}"
            return should, token

        day_name = parts[1].lower()
        hh, mm = int(parts[2]), int(parts[3])
        day_idx = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }.get(day_name, -1)
        if day_idx < 0 or now.weekday() != day_idx:
            return False, ""
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < slot:
            return False, ""
        should = last_run_ts <= (slot - timedelta(seconds=1)).timestamp()
        token = f"weekly:{day_name}:{slot.strftime('%Y%m%d%H%M')}"
        return should, token

    def _catch_up_on_start(self) -> None:
        state = self._engine._state_load()  # noqa: SLF001
        last_runs = dict(state.get("last_runs") or {})
        for wf_name, trigger in self._engine.get_scheduled_workflows():
            last = float(last_runs.get(wf_name) or 0.0)
            should, slot_token = self._should_catch_up(trigger, last)
            if not should:
                continue
            dedupe = f"catchup:{wf_name}:{slot_token}"
            logger.info(f"Catch-up run for workflow '{wf_name}' token={dedupe}")
            self._run_workflow(wf_name, dedupe_token=dedupe)

    def _run_workflow(self, wf_name: str, dedupe_token: str | None = None):
        """Execute a scheduled workflow."""
        try:
            logger.info(f"Scheduled workflow '{wf_name}' starting")
            run = self._engine.execute(wf_name, dedupe_token=dedupe_token)
            if self._notify and run.status == "completed":
                result = run.context.get(f"step_notify_user_result") or run.context.get("step_format_review_result") or f"Workflow '{wf_name}' completed."
                self._notify(self._cfg.default_owner_user_id, "telegram", str(result))
            elif self._notify and run.status == "failed":
                self._notify(self._cfg.default_owner_user_id, "telegram", f"Scheduled workflow '{wf_name}' failed.")
        except Exception as e:
            logger.error(f"Scheduled workflow '{wf_name}' error: {e}")

    def _loop(self):
        while self._running:
            try:
                self._scheduler.run_pending()
            except Exception as e:
                logger.error(f"Workflow scheduler error: {e}")
            time.sleep(30)

    def start(self):
        """Parse triggers and start the scheduler background thread."""
        if self._running:
            return
        self._parse_and_register()
        self._catch_up_on_start()
        if not self._scheduler.get_jobs():
            logger.info("No scheduled workflows found, scheduler not starting")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"Workflow scheduler started with {len(self._scheduler.get_jobs())} job(s)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Workflow scheduler stopped")
