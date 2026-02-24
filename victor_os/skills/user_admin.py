"""
Victor-OS User Admin Skill
Owner-only commands for managing users, roles, data modes, and consent flags.
"""

import re
from typing import Optional

from skill_base import Skill, SkillManifest
from config import get_config

cfg = get_config()


class UserAdminSkill(Skill):

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="user_admin",
            display_name="User Administration",
            version="1.0.0",
            description="Manage Victor users — add, remove, view profiles, set roles and data modes.",
            triggers=[
                r"\badd user\b",
                r"\bremove user\b",
                r"\bdelete user\b",
                r"\blist users\b",
                r"\bwho.*using\b",
                r"\buser profile\b",
                r"\bgrant access\b",
                r"\brevoke access\b",
                r"\bset.*role\b",
                r"\bset.*data.?mode\b",
                r"\bset.*consent\b",
                r"\buser consent\b",
            ],
            schedule=None,
            enabled=True,
        )

    def get_tools(self):
        return [
            self.tool_add_user,
            self.tool_remove_user,
            self.tool_list_users,
            self.tool_view_profile,
            self.tool_set_role,
            self.tool_set_data_mode,
            self.tool_set_consent,
        ]

    def get_proactive_checks(self):
        return []

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def tool_add_user(
        self,
        telegram_id: str,
        name: str,
        role: str = "viewer",
        data_mode: str = "isolated",
    ) -> str:
        """
        Add a new user to Victor. Only the owner can call this.
        telegram_id: The user's Telegram numeric ID (they can find it via @userinfobot).
        name: Display name for the user.
        role: 'owner' | 'trusted' | 'viewer'
        data_mode: 'isolated' | 'shared' | 'collaborative'
        """
        try:
            from user_registry import get_user_registry, VALID_ROLES, VALID_DATA_MODES
            registry = get_user_registry()

            if role not in VALID_ROLES:
                return f"Invalid role '{role}'. Choose from: {', '.join(sorted(VALID_ROLES))}"
            if data_mode not in VALID_DATA_MODES:
                return f"Invalid data_mode '{data_mode}'. Choose from: {', '.join(sorted(VALID_DATA_MODES))}"

            existing = registry.get_user(telegram_id)
            if existing:
                return (
                    f"User {name} ({telegram_id}) already exists with role={existing.role}. "
                    f"Use set_role or set_data_mode to change their settings."
                )

            profile = registry.add_user(telegram_id, name, role=role, data_mode=data_mode)
            return (
                f"User added.\n"
                f"Name: {profile.name}\n"
                f"ID: {profile.telegram_id}\n"
                f"Role: {profile.role}\n"
                f"Data mode: {profile.data_mode}\n\n"
                f"They can now message Victor. Victor will send them an onboarding welcome."
            )
        except Exception as e:
            return f"Failed to add user: {e}"

    def tool_remove_user(self, telegram_id: str) -> str:
        """
        Remove a user from Victor. They will no longer be able to interact with it.
        Their memory data is NOT deleted — it stays in the vault.
        """
        try:
            from user_registry import get_user_registry
            registry = get_user_registry()
            user = registry.get_user(telegram_id)
            if not user:
                return f"No user found with Telegram ID {telegram_id}."
            if user.role == "owner":
                return "Cannot remove the owner account."
            removed = registry.remove_user(telegram_id)
            if removed:
                return f"User {user.name} ({telegram_id}) has been removed. Their memory data is retained."
            return f"Failed to remove user {telegram_id}."
        except Exception as e:
            return f"Failed to remove user: {e}"

    def tool_list_users(self) -> str:
        """
        List all users with their role, data mode, last seen time, and profile summary.
        Owner only.
        """
        try:
            from user_registry import get_user_registry
            registry = get_user_registry()
            users = registry.list_users()
            if not users:
                return "No users registered yet."
            lines = [f"Victor Users ({len(users)} total)\n{'='*40}"]
            for u in users:
                lines.append(registry.format_user_line(u))
            return "\n\n".join(lines)
        except Exception as e:
            return f"Failed to list users: {e}"

    def tool_view_profile(self, telegram_id: str) -> str:
        """
        View the full profile for a user. Owner can view any user; others can only view their own.
        """
        try:
            from user_registry import get_user_registry
            registry = get_user_registry()
            user = registry.get_user(telegram_id)
            if not user:
                return f"No user found with Telegram ID {telegram_id}."
            consent_on = [k for k, v in user.consent_flags.items() if v]
            consent_off = [k for k, v in user.consent_flags.items() if not v]
            return (
                f"Profile: {user.name}\n"
                f"Telegram ID: {user.telegram_id}\n"
                f"Role: {user.role}\n"
                f"Data mode: {user.data_mode}\n"
                f"Joined: {user.joined_at[:10] if user.joined_at else 'unknown'}\n"
                f"Last seen: {user.last_seen[:10] if user.last_seen else 'never'}\n"
                f"Observing: {', '.join(consent_on) or 'nothing'}\n"
                f"Not observing: {', '.join(consent_off) or 'nothing'}\n\n"
                f"Profile summary:\n{user.profile_summary or 'No profile built yet — needs more interactions.'}"
            )
        except Exception as e:
            return f"Failed to view profile: {e}"

    def tool_set_role(self, telegram_id: str, role: str) -> str:
        """
        Change a user's role. Valid roles: owner, trusted, viewer.
        Owner only.
        """
        try:
            from user_registry import get_user_registry, VALID_ROLES
            registry = get_user_registry()
            if role not in VALID_ROLES:
                return f"Invalid role '{role}'. Choose from: {', '.join(sorted(VALID_ROLES))}"
            user = registry.get_user(telegram_id)
            if not user:
                return f"No user found with Telegram ID {telegram_id}."
            registry.update_profile(telegram_id, role=role)
            return f"{user.name} ({telegram_id}) role updated to '{role}'."
        except Exception as e:
            return f"Failed to set role: {e}"

    def tool_set_data_mode(self, telegram_id: str, mode: str) -> str:
        """
        Change a user's data isolation mode.
        isolated: user sees only their own data.
        shared: user shares global knowledge pool but private layer is separate.
        collaborative: full shared pool — user can see other collaborative users' data.
        Owner only.
        """
        try:
            from user_registry import get_user_registry, VALID_DATA_MODES
            registry = get_user_registry()
            if mode not in VALID_DATA_MODES:
                return f"Invalid mode '{mode}'. Choose from: {', '.join(sorted(VALID_DATA_MODES))}"
            user = registry.get_user(telegram_id)
            if not user:
                return f"No user found with Telegram ID {telegram_id}."
            registry.update_profile(telegram_id, data_mode=mode)
            return f"{user.name} ({telegram_id}) data mode updated to '{mode}'."
        except Exception as e:
            return f"Failed to set data mode: {e}"

    def tool_set_consent(self, telegram_id: str, flag: str, enabled: bool) -> str:
        """
        Update a user's consent flag for passive observation.
        flag: messages | voice | images | files | urls | screen | browser | calendar | mic
        Note: 'messages' consent cannot be disabled.
        """
        try:
            from user_registry import get_user_registry, DEFAULT_CONSENT
            registry = get_user_registry()
            if flag not in DEFAULT_CONSENT:
                valid = ', '.join(sorted(DEFAULT_CONSENT.keys()))
                return f"Unknown flag '{flag}'. Valid flags: {valid}"
            user = registry.get_user(telegram_id)
            if not user:
                return f"No user found with Telegram ID {telegram_id}."
            registry.set_consent(telegram_id, flag, enabled)
            state = "enabled" if enabled else "disabled"
            return f"Consent flag '{flag}' {state} for {user.name} ({telegram_id})."
        except Exception as e:
            return f"Failed to set consent: {e}"


def create_skill():
    return UserAdminSkill()
