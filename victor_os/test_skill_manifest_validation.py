import os
import tempfile
from pathlib import Path

import skill_registry


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_invalid_manifest_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        skills_dir = Path(tmp) / "skills"
        _write_file(
            skills_dir / "manifest.schema.json",
            """
{
  "type":"object",
  "properties":{
    "id":{"type":"string"},
    "name":{"type":"string"},
    "version":{"type":"string"},
    "description":{"type":"string"},
    "triggers":{"type":"array"},
    "required_env":{"type":"array"},
    "config_schema":{"type":"object"},
    "risk_profile":{"type":"string"},
    "permissions_required":{"type":"array"},
    "tools_exposed":{"type":"array"}
  },
  "required":["id","name","version","description","triggers","required_env","config_schema","risk_profile","permissions_required","tools_exposed"]
}
""".strip(),
        )
        _write_file(
            skills_dir / "bad_skill.py",
            """
from skill_base import Skill, SkillManifest
class BadSkill(Skill):
    def manifest(self):
        return SkillManifest(
            name="bad_skill",
            display_name="Bad",
            version="1.0",
            description="bad semver",
            triggers=[r".*"],
            config_schema={"type":"object","properties":{}},
            tools_exposed=("x",),
        )
    def get_tools(self):
        return []
def create_skill():
    return BadSkill()
""".strip(),
        )

        old_skills = skill_registry.SKILLS_DIR
        old_schema = skill_registry.MANIFEST_SCHEMA_PATH
        try:
            skill_registry.SKILLS_DIR = str(skills_dir)
            skill_registry.MANIFEST_SCHEMA_PATH = str(skills_dir / "manifest.schema.json")
            reg = skill_registry.SkillRegistry()
            loaded = reg.discover_and_load()
            assert loaded == 0
            assert reg.loaded_skills == {}
        finally:
            skill_registry.SKILLS_DIR = old_skills
            skill_registry.MANIFEST_SCHEMA_PATH = old_schema


def test_unknown_config_keys_are_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        skills_dir = Path(tmp) / "skills"
        _write_file(
            skills_dir / "manifest.schema.json",
            """
{
  "type":"object",
  "properties":{
    "id":{"type":"string"},
    "name":{"type":"string"},
    "version":{"type":"string"},
    "description":{"type":"string"},
    "triggers":{"type":"array"},
    "required_env":{"type":"array"},
    "config_schema":{"type":"object"},
    "risk_profile":{"type":"string"},
    "permissions_required":{"type":"array"},
    "tools_exposed":{"type":"array"}
  },
  "required":["id","name","version","description","triggers","required_env","config_schema","risk_profile","permissions_required","tools_exposed"]
}
""".strip(),
        )
        _write_file(
            skills_dir / "good_skill.py",
            """
from skill_base import Skill, SkillManifest
class GoodSkill(Skill):
    def manifest(self):
        return SkillManifest(
            name="good_skill",
            display_name="Good",
            version="1.0.0",
            description="good",
            triggers=[r".*"],
            config_schema={"type":"object","properties":{"enabled_flag":{"type":"boolean"}}},
            tools_exposed=("x",),
        )
    def get_tools(self):
        return []
def create_skill():
    return GoodSkill()
""".strip(),
        )

        old_skills = skill_registry.SKILLS_DIR
        old_schema = skill_registry.MANIFEST_SCHEMA_PATH
        old_override = os.environ.get("VICTOR_SKILL_CONFIG_OVERRIDES")
        try:
            os.environ["VICTOR_SKILL_CONFIG_OVERRIDES"] = '{"good_skill":{"unknown_key":true}}'
            skill_registry.SKILLS_DIR = str(skills_dir)
            skill_registry.MANIFEST_SCHEMA_PATH = str(skills_dir / "manifest.schema.json")
            reg = skill_registry.SkillRegistry()
            loaded = reg.discover_and_load()
            assert loaded == 0
        finally:
            if old_override is None:
                os.environ.pop("VICTOR_SKILL_CONFIG_OVERRIDES", None)
            else:
                os.environ["VICTOR_SKILL_CONFIG_OVERRIDES"] = old_override
            skill_registry.SKILLS_DIR = old_skills
            skill_registry.MANIFEST_SCHEMA_PATH = old_schema

