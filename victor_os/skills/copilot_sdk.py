"""
Copilot SDK skill for Victor-OS (optional integration).
"""

from __future__ import annotations

from typing import Callable

from skill_base import Skill, SkillManifest


class CopilotSdkSkill(Skill):
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="copilot_sdk",
            display_name="Copilot SDK Bridge",
            version="1.0.0",
            description="Checks Copilot SDK/CLI status and runs explicit Copilot prompts on demand.",
            triggers=[
                r"\bcopilot status\b",
                r"\bcopilot sdk\b",
                r"\bcopilot test\b",
                r"\bask copilot\b",
            ],
            enabled=True,
        )

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_copilot_status,
            self.tool_copilot_test,
            self.tool_copilot_prompt,
        ]

    def tool_copilot_status(self) -> str:
        try:
            from copilot_bridge import get_copilot_bridge

            stat = get_copilot_bridge().status()
            return (
                "Copilot SDK Status\n"
                f"- sdk_installed: {stat.get('sdk_installed')}\n"
                f"- cli_found: {stat.get('cli_found')}\n"
                f"- cli_path: {stat.get('cli_path')}\n"
                f"- default_model: {stat.get('default_model')}\n"
                f"- error: {stat.get('error') or 'none'}"
            )
        except Exception as exc:
            return f"Copilot status check failed: {exc}"

    def tool_copilot_test(self) -> str:
        try:
            from copilot_bridge import get_copilot_bridge

            return get_copilot_bridge().ask("Reply in one line: Copilot bridge online.")
        except Exception as exc:
            return f"Copilot test failed: {exc}"

    def tool_copilot_prompt(self, prompt: str, model: str = "") -> str:
        try:
            from copilot_bridge import get_copilot_bridge

            return get_copilot_bridge().ask(prompt=prompt, model=(model.strip() or None))
        except Exception as exc:
            return f"Copilot prompt failed: {exc}"


def create_skill():
    return CopilotSdkSkill()

