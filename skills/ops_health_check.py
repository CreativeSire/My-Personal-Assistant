import json
import os
import sqlite3
from typing import Callable

from config import get_config
from monitor import get_system_metrics
from skill_base import Skill, SkillManifest


class OpsHealthCheckSkill(Skill):
    def __init__(self):
        self._cfg = get_config()
        self._task_db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory_store", "victor_tasks.db")

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="ops_health_check",
            display_name="Ops Health Check",
            version="1.0.0",
            description="Monitors CPU/RAM/disk and queue depth with proactive alerts.",
            triggers=[r"\bhealth check\b", r"\bsystem status\b", r"\bops status\b"],
        )

    def _queue_depth(self) -> int:
        if not os.path.exists(self._task_db):
            return 0
        conn = sqlite3.connect(self._task_db)
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('pending','running')")
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    def tool_ops_health_summary(self) -> str:
        metrics = get_system_metrics()
        depth = self._queue_depth()
        return (
            "Ops Health Summary\n"
            f"- CPU: {metrics.get('cpu', 0)}%\n"
            f"- RAM: {metrics.get('ram', 0)}%\n"
            f"- DISK: {metrics.get('disk', 0)}%\n"
            f"- Queue depth: {depth}\n"
        )

    def get_tools(self) -> list[Callable]:
        return [self.tool_ops_health_summary]

    def get_proactive_checks(self) -> list[dict]:
        def _check():
            metrics = get_system_metrics()
            depth = self._queue_depth()
            cpu = float(metrics.get("cpu", 0))
            ram = float(metrics.get("ram", 0))
            disk = float(metrics.get("disk", 0))
            critical = (
                ram >= float(self._cfg.critical_ram_threshold)
                or disk >= float(self._cfg.critical_disk_threshold)
                or depth >= float(self._cfg.critical_queue_depth_threshold)
            )
            if critical:
                return json.dumps(
                    {
                        "alert": "ops_health_threshold",
                        "severity": "critical",
                        "cpu": cpu,
                        "ram": ram,
                        "disk": disk,
                        "queue_depth": depth,
                    },
                    ensure_ascii=False,
                )
            return None

        return [
            {
                "name": "ops_health_threshold",
                "interval_seconds": 300,
                "callback": _check,
                "channel_hint": "telegram",
                "user_id": "ceejay",
            }
        ]


def create_skill():
    return OpsHealthCheckSkill()
