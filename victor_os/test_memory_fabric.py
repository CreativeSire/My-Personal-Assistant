import os
import tempfile
import unittest
from unittest.mock import patch


os.environ.setdefault("VICTOR_MEMORY_KEY", "q06Wt0lQ4Vqb71ug-S8fV9hxv9DfQ_vkoHk4I0AH11Y=")

import memory_core  # noqa: E402
from memory_core import MemoryQueryInput, MemoryRecordInput  # noqa: E402
import memory_policy  # noqa: E402
from sitrep_builder import build_executive_sitrep  # noqa: E402


class TestMemoryCore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "vault.db")

        def fake_embed(text):
            text = (text or "").lower()
            base = [0.0, 0.0, 0.0, 0.0]
            if "python" in text:
                base[0] = 1.0
            if "deadline" in text or "2026" in text:
                base[1] = 1.0
            if "preference" in text or "prefer" in text:
                base[2] = 1.0
            if "risk" in text or "blocker" in text:
                base[3] = 1.0
            if sum(base) == 0:
                base = [1.0, 0.0, 0.0, 0.0]
            return base

        self.embed_patcher = patch.object(memory_core, "get_embedding", side_effect=fake_embed)
        self.embed_patcher.start()
        memory_core.DB_PATH = self.db_path
        memory_core.init_db()

    def tearDown(self):
        self.embed_patcher.stop()
        self.temp_dir.cleanup()

    def test_save_recall_roundtrip_and_scope(self):
        save_result = memory_core.save_memory(
            MemoryRecordInput(
                text="Project Python migration deadline is 2026-03-01",
                memory_type="task",
                scope="global",
                priority=5,
                source="test",
            )
        )
        self.assertTrue(save_result["ok"])

        memory_core.save_memory(
            MemoryRecordInput(
                text="Dev handoff for Python parser",
                memory_type="status",
                scope="agent",
                agent_name="Dev_Agent",
                source="test",
            )
        )

        global_result = memory_core.recall_memory(
            MemoryQueryInput(query="python deadline", scope_filter="global", top_k=3, min_similarity=0.1)
        )
        self.assertTrue(global_result["ok"])
        self.assertGreaterEqual(len(global_result["results"]), 1)
        self.assertEqual(global_result["results"][0]["scope"], "global")

        agent_result = memory_core.recall_memory(
            MemoryQueryInput(query="python parser", scope_filter="agent", agent_filter="Dev_Agent", top_k=3, min_similarity=0.1)
        )
        self.assertTrue(agent_result["ok"])
        self.assertGreaterEqual(len(agent_result["results"]), 1)
        self.assertEqual(agent_result["results"][0]["agent_name"], "Dev_Agent")

    def test_dedup_merge(self):
        first = memory_core.save_memory(
            MemoryRecordInput(
                text="CeeJay prefers concise executive responses.",
                memory_type="preference",
                scope="global",
                tags=["pref"],
                source="test",
            )
        )
        second = memory_core.save_memory(
            MemoryRecordInput(
                text="CeeJay prefers concise executive responses.",
                memory_type="preference",
                scope="global",
                tags=["style"],
                source="test",
            )
        )
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(second["status"], "dedup_merged")


class TestMemoryPolicy(unittest.TestCase):
    def test_sensitive_redaction(self):
        text = "API_KEY=abcd1234xyz and email me at demo@example.com"
        redacted, refs = memory_policy.redact_sensitive(text)
        self.assertNotIn("abcd1234xyz", redacted)
        self.assertTrue(refs)

    def test_deterministic_classification(self):
        user_text = "I prefer markdown reports. Deadline is 2026-03-01. We decided to use Memory Fabric v3."
        result = memory_policy.classify_memory_candidate(user_text, "I will handle this.", {"tool_names": ["execute_python_code"]})
        types = sorted({r["memory_type"] for r in result})
        self.assertIn("preference", types)
        self.assertIn("task", types)
        self.assertIn("decision", types)


class TestSitrepBuilder(unittest.TestCase):
    def test_sitrep_complete(self):
        report = build_executive_sitrep(
            mission=["Protect memory integrity"],
            projects=["Victor Memory Fabric v3"],
            decisions=["Use hybrid memory scope"],
            blockers=["Pending API quota review"],
            deadlines=["Finalize by 2026-03-01"],
            next_actions=["Run integration tests"],
        )
        self.assertIn("Executive Sitrep", report)
        self.assertIn("1. Mission Focus", report)
        self.assertNotIn("Data Gap Summary", report)

    def test_sitrep_sparse(self):
        report = build_executive_sitrep([], [], [], [], [], [])
        self.assertIn("Data Gap Summary", report)
        self.assertIn("Missing categories", report)


if __name__ == "__main__":
    unittest.main()
