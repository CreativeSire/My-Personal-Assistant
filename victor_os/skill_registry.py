"""
Victor-OS Skill Registry
Auto-discovers, loads, and manages skills from the skills/ directory.
"""

import json
import os
import re
import sys
import warnings
import importlib.util
from typing import Any

from skill_base import Skill, SkillManifest
from logging_config import get_logger

logger = get_logger("skill_registry")

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
MANIFEST_SCHEMA_PATH = os.path.join(SKILLS_DIR, "manifest.schema.json")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _load_skill_config_overrides() -> dict[str, dict[str, Any]]:
    raw = str(os.getenv("VICTOR_SKILL_CONFIG_OVERRIDES", "")).strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        logger.warning("Invalid VICTOR_SKILL_CONFIG_OVERRIDES JSON; ignoring")
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in parsed.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = v
    return out


def _load_manifest_schema() -> dict[str, Any]:
    try:
        with open(MANIFEST_SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema = json.load(f)
        if isinstance(schema, dict):
            return schema
    except Exception as exc:
        logger.warning(f"Failed to load manifest schema: {exc}")
    return {}


def _validate_manifest_doc(doc: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    props = schema.get("properties") if isinstance(schema, dict) else {}
    required = schema.get("required") if isinstance(schema, dict) else []
    allowed_keys = set(props.keys()) if isinstance(props, dict) else set()
    if isinstance(required, list):
        for key in required:
            if key not in doc:
                errors.append(f"missing required key: {key}")
    for key in doc:
        if allowed_keys and key not in allowed_keys:
            errors.append(f"unknown key: {key}")
    version = str(doc.get("version") or "")
    if version and not re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", version):
        errors.append("version must be semver format (x.y.z)")
    risk = str(doc.get("risk_profile") or "medium").strip().lower()
    if risk and risk not in {"low", "medium", "high"}:
        errors.append("risk_profile must be one of low|medium|high")
    return errors


def _manifest_doc_from_skill(manifest: SkillManifest) -> dict[str, Any]:
    return {
        "id": manifest.name,
        "name": manifest.display_name,
        "version": manifest.version,
        "description": manifest.description,
        "triggers": list(manifest.triggers or []),
        "required_env": list(manifest.required_env or []),
        "config_schema": dict(manifest.config_schema or {}),
        "risk_profile": str(manifest.risk_profile or "medium"),
        "permissions_required": list(manifest.permissions_required or []),
        "tools_exposed": list(manifest.tools_exposed or []),
    }


_PRIVATE_IMPORT_PATTERNS = [
    r"^\s*from\s+config\s+import\s+",
    r"^\s*from\s+memory_core\s+import\s+",
    r"^\s*from\s+data_engine\s+import\s+",
    r"^\s*from\s+session_manager\s+import\s+",
]


def _warn_if_private_imports(module: Any, manifest_name: str) -> None:
    module_path = str(getattr(module, "__file__", "") or "")
    if not module_path or not os.path.exists(module_path):
        return
    try:
        with open(module_path, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception:
        return
    if "from sdk import" in source or "from sdk.api import" in source:
        return
    flags = []
    for p in _PRIVATE_IMPORT_PATTERNS:
        if re.search(p, source, re.MULTILINE):
            flags.append(p)
    if flags:
        warnings.warn(
            f"[DEPRECATION] Skill '{manifest_name}' imports private runtime modules directly. "
            "Migrate to victor_os/sdk extension API.",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning(
            f"Skill '{manifest_name}' uses private imports; SDK migration recommended.",
            extra={"skill_name": manifest_name},
        )


class SkillRegistry:
    """Discovers, loads, and manages skills."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._manifests: dict[str, SkillManifest] = {}
        self._tools: dict[str, list] = {}
        self._schema = _load_manifest_schema()
        self._skill_config_overrides = _load_skill_config_overrides()

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

    def _validate_runtime_manifest(self, module_name: str, manifest: SkillManifest) -> None:
        errors = _validate_manifest_doc(_manifest_doc_from_skill(manifest), self._schema)
        if errors:
            raise ValueError(f"Invalid manifest for skill '{module_name}': " + "; ".join(errors))

    def _validate_skill_config(self, module_name: str, manifest: SkillManifest) -> None:
        override = self._skill_config_overrides.get(module_name) or self._skill_config_overrides.get(manifest.name) or {}
        if not isinstance(override, dict) or not override:
            return
        schema = manifest.config_schema if isinstance(manifest.config_schema, dict) else {}
        props = schema.get("properties") if isinstance(schema, dict) else {}
        allowed = set(props.keys()) if isinstance(props, dict) else set()
        unknown = sorted([k for k in override.keys() if k not in allowed])
        if unknown:
            raise ValueError(
                f"Unknown config keys for skill '{manifest.name}': {', '.join(unknown)}. "
                f"Allowed: {', '.join(sorted(allowed)) or '(none)'}"
            )

    def _register_from_module(self, name: str, module: Any) -> None:
        if not hasattr(module, "create_skill"):
            raise AttributeError(f"Skill module '{name}' missing create_skill() factory")

        from google.adk.tools.function_tool import FunctionTool

        skill: Skill = module.create_skill()
        manifest = skill.manifest()
        _warn_if_private_imports(module, manifest.name)
        self._validate_runtime_manifest(name, manifest)
        self._validate_skill_config(name, manifest)

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
        all_tools = []
        for tools in self._tools.values():
            all_tools.extend(tools)
        return all_tools

    def match_trigger(self, user_text: str) -> list[str]:
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
        checks = []
        for name, skill in self._skills.items():
            for check in skill.get_proactive_checks():
                check["skill_name"] = name
                checks.append(check)
        return checks
