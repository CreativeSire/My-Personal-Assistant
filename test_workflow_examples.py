import unittest

from workflow_engine import WorkflowEngine


class TestWorkflowExamples(unittest.TestCase):
    def setUp(self):
        self.engine = WorkflowEngine()
        self.engine.load_definitions_from_dir()

        self.engine.register_action("ops_health_summary", lambda params, context: "ops-ok")
        self.engine.register_action("compose_daily_ops_report", lambda params, context: "daily-report")
        self.engine.register_action("market_snapshot", lambda params, context: "market-ok")
        self.engine.register_action("memory_hygiene", lambda params, context: "memory-ok")
        self.engine.register_action("notify", lambda params, context: params.get("message", "notified"))

    def test_workflows_load(self):
        names = [w["name"] for w in self.engine.get_available_workflows()]
        self.assertIn("ops_daily_brief", names)
        self.assertIn("market_watch_alert", names)
        self.assertIn("memory_hygiene_report", names)

    def test_execute_ops_daily_brief(self):
        run = self.engine.execute("ops_daily_brief")
        self.assertEqual(run.status, "completed")
        self.assertIn("step_notify_user_result", run.context)

    def test_execute_market_watch_alert(self):
        run = self.engine.execute("market_watch_alert")
        self.assertEqual(run.status, "completed")
        self.assertIn("step_notify_user_result", run.context)


if __name__ == "__main__":
    unittest.main()
