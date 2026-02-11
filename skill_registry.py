"""
Victor-OS Skill Registry
Auto-discovers, loads, and manages skills from the skills/ directory.
"""

import os
import re
import sys
import importlib
import importlib.util
from typing import Any

from skill_base import Skill, SkillManifest
from logging_config import get_logger

logger = get_logger("skill_registry")

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")


class SkillRegistry:
    """Discovers, loads, and manages skills."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._manifests: dict[str, SkillManifest] = {}
        self._tools: dict[str, list] = {}  # name -> list of FunctionTool

    @property
    def loaded_skills(self) -> dict[str, SkillManifest]:
        return dict(self._manifests)

    def discover_and_load(self) -> int:
        """Auto-discover all skills in skills/ directory. Returns count loaded."""
        os.makedirs(SKILLS_DIR, exist_ok=True)
        count = 0
        for entry in os.listdir(SKILLS_DIR):
            skill_dir = os.path.join(SKILLS_DIR, entry)
            init_file = os.path.join(skill_dir, "__init__.py")
            if os.path.isdir(skill_dir) and os.path.exists(init_file):
                try:
                    self._load_skill_from_dir(entry, skill_dir)
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to load skill '{entry}': {e}")
            elif entry.endswith(".py") and entry != "__init__.py":
                skill_name = entry[:-3]
                try:
                    self._load_skill_from_file(skill_name, os.path.join(SKILLS_DIR, entry))
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to load skill '{skill_name}': {e}")
        logger.info(f"Loaded {count} skill(s) from {SKILLS_DIR}")
        return count

    def _load_skill_from_dir(self, name: str, path: str) -> None:
        module_name = f"skills.{name}"
        # Remove cached module to allow hot-reload
        if module_name in sys.modules:
            del sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, os.path.join(path, "__init__.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        self._register_from_module(name, module)

    def _load_skill_from_file(self, name: str, path: str) -> None:
        module_name = f"skills.{name}"
        if module_name in sys.modules:
            del sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        self._register_from_module(name, module)

    def _register_from_module(self, name: str, module: Any) -> None:
        if not hasattr(module, "create_skill"):
            raise AttributeError(f"Skill module '{name}' missing create_skill() factory")

        from google.adk.tools.function_tool import FunctionTool

        skill: Skill = module.create_skill()
        manifest = skill.manifest()
        if not manifest.enabled:
            logger.info(f"Skill '{manifest.name}' is disabled, skipping")
            return
        skill.on_load()
        tools = [FunctionTool(fn) for fn in skill.get_tools()]
        self._skills[manifest.name] = skill
        self._manifests[manifest.name] = manifest
        self._tools[manifest.name] = tools
        logger.info(f"Registered skill: {manifest.name} v{manifest.version} ({len(tools)} tools)")

    def get_all_tools(self) -> list:
        """Return all FunctionTools from all enabled skills."""
        all_tools = []
        for tools in self._tools.values():
            all_tools.extend(tools)
        return all_tools

    def match_trigger(self, user_text: str) -> list[str]:
        """Return names of skills whose trigger patterns match the input."""
        matches = []
        for name, manifest in self._manifests.items():
            for pattern in manifest.triggers:
                if re.search(pattern, user_text, re.IGNORECASE):
                    matches.append(name)
                    break
        return matches

    def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def unload_skill(self, name: str) -> bool:
        if name in self._skills:
            self._skills[name].on_unload()
            del self._skills[name]
            del self._manifests[name]
            del self._tools[name]
            logger.info(f"Unloaded skill: {name}")
            return True
        return False

    def reload_skill(self, name: str) -> bool:
        """Hot-reload a single skill by name."""
        self.unload_skill(name)
        dir_path = os.path.join(SKILLS_DIR, name)
        file_path = os.path.join(SKILLS_DIR, f"{name}.py")
        try:
            if os.path.isdir(dir_path):
                self._load_skill_from_dir(name, dir_path)
            elif os.path.exists(file_path):
                self._load_skill_from_file(name, file_path)
            else:
                return False
            return True
        except Exception as e:
            logger.error(f"Reload failed for '{name}': {e}")
            return False

    def get_proactive_checks(self) -> list[dict[str, Any]]:
        """Collect all proactive checks from all skills."""
        checks = []
        for name, skill in self._skills.items():
            for check in skill.get_proactive_checks():
                check["skill_name"] = name
                checks.append(check)
        return checks
