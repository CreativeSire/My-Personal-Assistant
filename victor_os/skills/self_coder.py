import os
import re
import shutil
import time
from typing import Callable

from config import get_config
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("self_coder_skill")


class SelfCoderSkill(Skill):
    def __init__(self):
        self._cfg = get_config()
        self._workspace = os.path.join(self._cfg.base_dir, str(self._cfg.self_coder_workspace or "workspace/proposed_changes"))
        os.makedirs(self._workspace, exist_ok=True)

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="self_coder",
            display_name="Self Coder",
            version="1.0.0",
            description="Proposes new skills into a safe workspace; deploy requires explicit approval.",
            triggers=[r"\bpropose skill\b", r"\bwrite a skill\b", r"\bself coder\b"],
            enabled=True,
        )

    def tool_propose_skill(self, description: str) -> str:
        if not bool(getattr(self._cfg, "self_coder_enabled", False)):
            return "Self-coder is disabled (SELF_CODER_ENABLED=false)."
        desc = str(description or "").strip()
        if not desc:
            return "Description is required."

        # Minimal generation: requires anthropic client if installed and key present.
        api_key = str(getattr(self._cfg, "anthropic_api_key", "") or "").strip()
        if not api_key:
            return "ANTHROPIC_API_KEY not set; cannot generate proposal."
        try:
            import anthropic  # type: ignore
        except Exception as exc:
            return f"anthropic library missing: {exc}"

        template_path = os.path.join(self._cfg.base_dir, "skills", "market_watch.py")
        base_path = os.path.join(self._cfg.base_dir, "skill_base.py")
        try:
            template = open(template_path, "r", encoding="utf-8").read()
        except Exception:
            template = ""
        try:
            base = open(base_path, "r", encoding="utf-8").read()
        except Exception:
            base = ""

        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            f"Write a Victor-OS skill for: {desc}\n\n"
            "Constraints:\n"
            "- Follow the Skill / SkillManifest pattern.\n"
            "- Provide create_skill() factory.\n"
            "- Avoid network calls unless explicitly requested.\n\n"
            f"Template:\n{template}\n\n"
            f"Skill Base:\n{base}\n"
        )
        msg = client.messages.create(
            model=os.getenv("SELF_CODER_MODEL", "claude-3-5-sonnet-latest"),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        content = ""
        try:
            content = msg.content[0].text  # type: ignore[attr-defined]
        except Exception:
            content = str(msg)

        # Save proposal (never writes directly into skills/).
        # Use a timestamped unique name so proposals never overwrite each other.
        ts = time.strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "_", description[:40].lower()).strip("_") or "skill"
        safe_name = f"proposal_{slug}_{ts}.py"
        out_path = os.path.join(self._workspace, safe_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Proposal written: {os.path.relpath(out_path, self._cfg.base_dir)}"

    def tool_list_proposals(self) -> str:
        if not os.path.isdir(self._workspace):
            return "No proposal workspace."
        files = [f for f in os.listdir(self._workspace) if os.path.isfile(os.path.join(self._workspace, f))]
        if not files:
            return "No proposals."
        return "Proposals:\n" + "\n".join(f"- {name}" for name in sorted(files)[:200])

    def tool_review_proposal(self, filename: str) -> str:
        name = os.path.basename(str(filename or "").strip())
        if not name:
            return "filename is required"
        path = os.path.join(self._workspace, name)
        if not os.path.exists(path):
            return "proposal not found"
        text = open(path, "r", encoding="utf-8", errors="replace").read()
        return text[:8000]

    def tool_approve_proposal(self, filename: str, approval: str = "") -> str:
        if str(approval or "").strip().lower() not in {"approve", "yes", "y"}:
            return "Approval required. Re-run with approval='approve'."
        name = os.path.basename(str(filename or "").strip())
        if not name:
            return "filename is required"
        src = os.path.join(self._workspace, name)
        if not os.path.exists(src):
            return "proposal not found"
        # Deploy into skills/ as a .py file (no package dirs).
        dest_name = name if name.endswith(".py") else f"{name}.py"
        dest = os.path.join(self._cfg.base_dir, "skills", dest_name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
        # Try hot reload.
        try:
            from agents import get_skill_registry  # type: ignore

            reg = get_skill_registry()
            if reg:
                reg.reload_skill(dest_name[:-3] if dest_name.endswith(".py") else dest_name)
        except Exception as exc:
            logger.warning(f"Skill reload failed: {exc}")
        return f"Deployed skill: skills/{dest_name}"

    def tool_reject_proposal(self, filename: str) -> str:
        name = os.path.basename(str(filename or "").strip())
        if not name:
            return "filename is required"
        path = os.path.join(self._workspace, name)
        if not os.path.exists(path):
            return "proposal not found"
        os.remove(path)
        return f"Rejected and removed: {name}"

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_propose_skill,
            self.tool_list_proposals,
            self.tool_review_proposal,
            self.tool_approve_proposal,
            self.tool_reject_proposal,
        ]


def create_skill():
    return SelfCoderSkill()

