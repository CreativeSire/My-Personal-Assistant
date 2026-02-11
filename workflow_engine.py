"""
Victor-OS Workflow Engine
JSON-defined multi-step workflow executor with conditional branching.
"""

import os
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import schedule as schedule_lib

from logging_config import get_logger

logger = get_logger("workflow_engine")


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
    completed_at: float | None = None


class WorkflowEngine:
    """Executes multi-step workflows defined as JSON configs."""

    def __init__(self):
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._action_handlers: dict[str, Callable] = {}
        self._workflows_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "workflows"
        )

    def register_action(self, name: str, handler: Callable[[dict[str, Any], dict[str, Any]], str]) -> None:
        """Register an action handler. Signature: handler(params, context) -> result_string."""
        self._action_handlers[name] = handler

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

    def execute(self, workflow_name: str, initial_context: dict[str, Any] | None = None) -> WorkflowRun:
        """Execute a workflow synchronously. Returns the completed WorkflowRun."""
        wf = self._definitions.get(workflow_name)
        if not wf:
            raise ValueError(f"Unknown workflow: {workflow_name}")

        run = WorkflowRun(
            workflow_name=workflow_name,
            run_id=uuid.uuid4().hex[:12],
            status="running",
            current_step=0,
            context=initial_context or {},
            step_results=[],
            started_at=time.time(),
        )

        logger.info(f"Starting workflow '{workflow_name}' run={run.run_id}")

        step_index = 0
        steps_by_name = {s.name: i for i, s in enumerate(wf.steps)}

        while step_index < len(wf.steps):
            step = wf.steps[step_index]
            run.current_step = step_index

            # Evaluate condition
            if step.condition:
                try:
                    if not eval(step.condition, {"__builtins__": {}}, run.context):
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

            attempts = step.retries + 1
            success = False
            result = ""
            for attempt in range(attempts):
                try:
                    # Resolve $ctx. references in params
                    resolved_params = {}
                    for k, v in step.params.items():
                        if isinstance(v, str) and v.startswith("$ctx."):
                            ctx_key = v[5:]
                            resolved_params[k] = run.context.get(ctx_key, v)
                        else:
                            resolved_params[k] = v

                    result = handler(resolved_params, run.context)
                    success = True
                    break
                except Exception as e:
                    logger.warning(f"Step '{step.name}' attempt {attempt + 1} failed: {e}")
                    if attempt == attempts - 1:
                        result = str(e)

            step_result = {
                "step": step.name,
                "status": "completed" if success else "failed",
                "result": result,
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
        logger.info(f"Workflow '{workflow_name}' run={run.run_id} finished: {run.status}")
        return run

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
        user_id: str = "ceejay",
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

    def _run_workflow(self, wf_name: str):
        """Execute a scheduled workflow."""
        try:
            logger.info(f"Scheduled workflow '{wf_name}' starting")
            run = self._engine.execute(wf_name)
            if self._notify and run.status == "completed":
                result = run.context.get(f"step_notify_user_result") or run.context.get("step_format_review_result") or f"Workflow '{wf_name}' completed."
                self._notify("ceejay", "telegram", str(result))
            elif self._notify and run.status == "failed":
                self._notify("ceejay", "telegram", f"Scheduled workflow '{wf_name}' failed.")
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
