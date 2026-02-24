"""
Victor-OS Planner Agent
Separate ADK agent that decomposes complex goals into step-by-step execution plans.
Plans are persisted to SQLite so they survive restarts.
Chief_of_Staff delegates to this agent for multi-step requests.
"""

import json
import os
import sqlite3
import time
import uuid
from typing import Any

from google.adk import Agent
from google.adk.tools.function_tool import FunctionTool

from config import get_config
from errors import PlannerError
from logging_config import get_logger

logger = get_logger("planner_agent")
cfg = get_config()

_DB_PATH = os.path.join(cfg.base_dir, "memory_store", "victor_plans.db")


# ---------------------------------------------------------------------------
# SQLite Plan Store
# ---------------------------------------------------------------------------

class PlanStore:
    """SQLite-backed plan persistence. Plans survive restarts."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                steps TEXT DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL
            )
        """)
        conn.commit()
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def create(self, plan_id: str, goal: str) -> dict[str, Any]:
        now = time.time()
        plan = {
            "plan_id": plan_id,
            "goal": goal,
            "status": "active",
            "steps": [],
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        conn = self._conn()
        conn.execute(
            "INSERT INTO plans (id, goal, status, steps, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (plan_id, goal, "active", "[]", now, now),
        )
        conn.commit()
        conn.close()
        return plan

    def get(self, plan_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        row = conn.execute("SELECT id, goal, status, steps, created_at, updated_at, completed_at FROM plans WHERE id = ?", (plan_id,)).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "plan_id": row[0],
            "goal": row[1],
            "status": row[2],
            "steps": json.loads(row[3]) if row[3] else [],
            "created_at": row[4],
            "updated_at": row[5],
            "completed_at": row[6],
        }

    def update(self, plan_id: str, **kwargs) -> bool:
        conn = self._conn()
        sets = ["updated_at = ?"]
        params: list[Any] = [time.time()]
        if "status" in kwargs:
            sets.append("status = ?")
            params.append(kwargs["status"])
        if "steps" in kwargs:
            sets.append("steps = ?")
            params.append(json.dumps(kwargs["steps"]))
        if "completed_at" in kwargs:
            sets.append("completed_at = ?")
            params.append(kwargs["completed_at"])
        params.append(plan_id)
        result = conn.execute(f"UPDATE plans SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        conn.close()
        return result.rowcount > 0

    def list_active(self) -> list[dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, goal, status, steps, created_at, updated_at, completed_at FROM plans WHERE status IN ('active', 'ready') ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        conn.close()
        return [
            {
                "plan_id": r[0], "goal": r[1], "status": r[2],
                "steps": json.loads(r[3]) if r[3] else [],
                "created_at": r[4], "updated_at": r[5], "completed_at": r[6],
            }
            for r in rows
        ]


_store = PlanStore()


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def create_execution_plan(goal_description: str) -> str:
    """Decompose a complex goal into a structured multi-step execution plan.

    Call this when a user request involves multiple distinct actions that
    should be performed in sequence or involve different specialist agents.

    Args:
        goal_description: The full user request or goal to decompose.

    Returns:
        A JSON execution plan with ordered steps, assigned agents, and dependencies.
    """
    plan_id = uuid.uuid4().hex[:10]
    _store.create(plan_id, goal_description)
    logger.info(f"Plan created: {plan_id} for goal: {goal_description[:120]}")

    return json.dumps(
        {
            "plan_id": plan_id,
            "status": "created",
            "instruction": (
                "You MUST now analyze the goal and return a JSON plan with this structure:\n"
                "{\n"
                '  "plan_id": "' + plan_id + '",\n'
                '  "goal": "<the goal>",\n'
                '  "steps": [\n'
                "    {\n"
                '      "step_number": 1,\n'
                '      "action": "<what to do>",\n'
                '      "assigned_agent": "<Research_Agent|Dev_Agent|Data_Scientist|Script_Agent|Academic_Writer>",\n'
                '      "description": "<detailed instruction for the agent>",\n'
                '      "depends_on": [],\n'
                '      "tools_needed": ["<tool_name>"]\n'
                "    }\n"
                "  ]\n"
                "}\n"
                "Return ONLY the JSON plan. Be specific about which agent handles each step."
            ),
        },
        indent=2,
    )


def save_plan_steps(plan_id: str, steps_json: str) -> str:
    """Save the decomposed steps into an active execution plan.

    Args:
        plan_id: The plan ID returned by create_execution_plan.
        steps_json: JSON array of step objects with step_number, action, assigned_agent, description, depends_on.

    Returns:
        Confirmation with the saved plan summary.
    """
    plan = _store.get(plan_id)
    if not plan:
        return json.dumps({"error": f"Plan {plan_id} not found."})

    try:
        steps = json.loads(steps_json)
        if isinstance(steps, dict) and "steps" in steps:
            steps = steps["steps"]
        if not isinstance(steps, list):
            return json.dumps({"error": "steps_json must be a JSON array of step objects."})
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON: {e}"})

    for i, step in enumerate(steps):
        step.setdefault("step_number", i + 1)
        step.setdefault("status", "pending")

    _store.update(plan_id, steps=steps, status="ready")
    logger.info(f"Plan {plan_id} saved with {len(steps)} steps")

    summary_lines = [f"Plan {plan_id}: {len(steps)} steps ready for execution"]
    for step in steps:
        agent = step.get("assigned_agent", "unassigned")
        action = step.get("action", step.get("description", ""))[:80]
        summary_lines.append(f"  Step {step.get('step_number')}: [{agent}] {action}")

    return "\n".join(summary_lines)


def get_plan_status(plan_id: str) -> str:
    """Check the current status of an execution plan.

    Args:
        plan_id: The plan ID to check.

    Returns:
        JSON status of the plan including step completion states.
    """
    plan = _store.get(plan_id)
    if not plan:
        return json.dumps({"error": f"Plan '{plan_id}' not found. It may have been completed or expired."})

    steps = plan.get("steps", [])
    total = len(steps)
    completed = sum(1 for s in steps if s.get("status") == "completed")
    failed = sum(1 for s in steps if s.get("status") == "failed")

    return json.dumps(
        {
            "plan_id": plan_id,
            "goal": plan.get("goal", ""),
            "status": plan.get("status", "unknown"),
            "total_steps": total,
            "completed": completed,
            "failed": failed,
            "steps": steps,
        },
        indent=2,
    )


def mark_plan_step(plan_id: str, step_number: int, status: str, result: str = "") -> str:
    """Mark a specific step in an execution plan as completed or failed.

    Args:
        plan_id: The plan ID.
        step_number: The step number to update (1-based).
        status: New status — 'completed' or 'failed'.
        result: Optional result text or error message.

    Returns:
        Confirmation of the update.
    """
    plan = _store.get(plan_id)
    if not plan:
        return f"Plan '{plan_id}' not found."

    steps = plan.get("steps", [])
    found = False
    for step in steps:
        if step.get("step_number") == step_number:
            step["status"] = status
            if result:
                step["result"] = result
            found = True
            logger.info(f"Plan {plan_id} step {step_number} -> {status}")
            break

    if not found:
        return f"Step {step_number} not found in plan {plan_id}."

    # Check if all steps are done
    plan_status = plan["status"]
    if steps and all(s.get("status") in ("completed", "failed", "skipped") for s in steps):
        plan_status = "completed"
        _store.update(plan_id, steps=steps, status="completed", completed_at=time.time())
    else:
        _store.update(plan_id, steps=steps)

    return f"Step {step_number} marked as '{status}'. Plan status: {plan_status}"


def list_active_plans() -> str:
    """List all currently active execution plans.

    Returns:
        Summary of active plans.
    """
    plans = _store.list_active()
    if not plans:
        return "No active plans."

    lines = []
    for plan in plans:
        goal = plan.get("goal", "")[:80]
        status = plan.get("status", "unknown")
        step_count = len(plan.get("steps", []))
        lines.append(f"- {plan['plan_id']}: [{status}] {goal} ({step_count} steps)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

plan_create_tool = FunctionTool(create_execution_plan)
plan_save_tool = FunctionTool(save_plan_steps)
plan_status_tool = FunctionTool(get_plan_status)
plan_step_tool = FunctionTool(mark_plan_step)
plan_list_tool = FunctionTool(list_active_plans)

# ---------------------------------------------------------------------------
# Planner Agent Definition
# ---------------------------------------------------------------------------

planner_agent = Agent(
    name="Planner_Agent",
    model=cfg.model_name,
    tools=[plan_create_tool, plan_save_tool, plan_status_tool, plan_step_tool, plan_list_tool],
    instruction="""
    ROLE: Strategic Planner & Goal Decomposer.

    You are the Planner Agent for Victor-OS. Your SOLE purpose is to take complex,
    multi-step goals and decompose them into clear, actionable execution plans.

    WHEN TO PLAN:
    - User requests that involve 2+ distinct actions (e.g., "research X, analyze it, and email me")
    - Goals that span multiple specialist domains (research + coding + communication)
    - Requests with implicit ordering/dependencies

    HOW TO PLAN:
    1. Call create_execution_plan(goal_description) to initialize a plan container.
    2. Analyze the goal and identify discrete steps.
    3. For each step, determine:
       - Which specialist agent should handle it (Research_Agent, Dev_Agent, Data_Scientist, Script_Agent, Academic_Writer)
       - What specific tools that agent will need
       - What dependencies exist (step 2 needs output from step 1)
    4. Call save_plan_steps(plan_id, steps_json) with the structured steps.
    5. Return the plan summary to Chief_of_Staff for execution.

    AGENT ASSIGNMENT GUIDE:
    - Research/facts/news/prices -> Research_Agent
    - Code/debug/system/git -> Dev_Agent
    - Data analysis/Excel/charts -> Data_Scientist
    - Email/voice/communication -> Script_Agent
    - Academic writing/papers -> Academic_Writer

    OUTPUT FORMAT:
    Always return a clear plan summary with numbered steps and agent assignments.
    Be specific about what each step should accomplish.
    Keep plans between 2-7 steps. If a goal needs more, break it into sub-goals.

    CRITICAL RULES:
    - DO NOT execute the plan steps yourself. Only plan them.
    - DO NOT make vague steps like "do the thing". Be specific.
    - Always assign an agent to each step.
    - Always consider dependencies between steps.
    """,
)
