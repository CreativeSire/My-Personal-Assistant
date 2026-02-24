import os
import datetime as dt
from typing import Callable

from config import get_config
from skill_base import Skill, SkillManifest


def _local_today() -> dt.date:
    return dt.datetime.now().date()


class CalendarSyncSkill(Skill):
    def __init__(self):
        self._cfg = get_config()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="calendar_sync",
            display_name="Calendar Sync",
            version="1.0.0",
            description="Google Calendar integration (agenda, create events, free slots).",
            triggers=[
                r"\bmy schedule\b",
                r"\bmy calendar\b",
                r"\bwhat.*today\b",
                r"\badd.*meeting\b",
                r"\bcreate.*event\b",
                r"\bam i free\b",
                r"\bupcoming events\b",
                r"\btoday's agenda\b",
            ],
            enabled=True,
        )

    def _is_configured(self) -> bool:
        return bool(self._cfg.google_calendar_credentials and self._cfg.google_calendar_token)

    def _get_service(self):
        if not self._is_configured():
            raise RuntimeError("Google Calendar is not configured. Set GOOGLE_CALENDAR_CREDENTIALS and GOOGLE_CALENDAR_TOKEN.")
        try:
            from google.oauth2.credentials import Credentials  # type: ignore
            from google.auth.transport.requests import Request  # type: ignore
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"Google calendar libraries missing: {exc}")

        scopes = ["https://www.googleapis.com/auth/calendar"]
        token_path = os.path.join(self._cfg.base_dir, self._cfg.google_calendar_token)
        creds_path = os.path.join(self._cfg.base_dir, self._cfg.google_calendar_credentials)
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, scopes)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
                creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        return build("calendar", "v3", credentials=creds)

    def tool_get_todays_agenda(self) -> str:
        if not self._is_configured():
            return "Calendar not configured."
        service = self._get_service()
        day = _local_today()
        start = dt.datetime.combine(day, dt.time.min).astimezone().isoformat()
        end = dt.datetime.combine(day, dt.time.max).astimezone().isoformat()
        events = (
            service.events()
            .list(
                calendarId=self._cfg.google_calendar_id or "primary",
                timeMin=start,
                timeMax=end,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )
        if not events:
            return "Today's agenda: no events."
        lines = ["Today's agenda:"]
        for e in events:
            s = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date") or ""
            summary = e.get("summary") or "(no title)"
            lines.append(f"- {s}: {summary}")
        return "\n".join(lines)

    def tool_get_upcoming_events(self, days: int = 7) -> str:
        if not self._is_configured():
            return "Calendar not configured."
        d = max(1, min(30, int(days)))
        service = self._get_service()
        now = dt.datetime.now().astimezone()
        end = now + dt.timedelta(days=d)
        events = (
            service.events()
            .list(
                calendarId=self._cfg.google_calendar_id or "primary",
                timeMin=now.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )
        if not events:
            return f"No upcoming events in next {d} day(s)."
        lines = [f"Upcoming events ({d} day(s)):"]
        for e in events[:50]:
            s = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date") or ""
            summary = e.get("summary") or "(no title)"
            lines.append(f"- {s}: {summary}")
        return "\n".join(lines)

    def tool_create_event(
        self,
        title: str,
        date: str,
        time: str,
        duration_minutes: int,
        description: str = "",
    ) -> str:
        if not self._is_configured():
            return "Calendar not configured."
        service = self._get_service()
        start_dt = dt.datetime.fromisoformat(f"{date}T{time}").astimezone()
        end_dt = start_dt + dt.timedelta(minutes=max(5, int(duration_minutes)))
        body = {
            "summary": title,
            "description": description or "",
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }
        created = service.events().insert(calendarId=self._cfg.google_calendar_id or "primary", body=body).execute()
        return f"Event created: {created.get('htmlLink') or created.get('id')}"

    def tool_check_free_slots(self, date: str) -> str:
        if not self._is_configured():
            return "Calendar not configured."
        # Simple: lists busy blocks; free slots computation can be added later.
        service = self._get_service()
        day = dt.date.fromisoformat(date)
        start = dt.datetime.combine(day, dt.time.min).astimezone().isoformat()
        end = dt.datetime.combine(day, dt.time.max).astimezone().isoformat()
        events = (
            service.events()
            .list(
                calendarId=self._cfg.google_calendar_id or "primary",
                timeMin=start,
                timeMax=end,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )
        if not events:
            return "No events that day. Likely fully free."
        lines = ["Busy blocks:"]
        for e in events[:50]:
            s = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date") or ""
            t = (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date") or ""
            summary = e.get("summary") or "(no title)"
            lines.append(f"- {s} -> {t}: {summary}")
        return "\n".join(lines)

    def _morning_calendar_brief(self) -> str | None:
        # Only fire between 07:30–08:30 local time.
        now = dt.datetime.now()
        if not (now.hour == 7 or now.hour == 8):
            return None
        if now.hour == 7 and now.minute < 30:
            return None
        if now.hour == 8 and now.minute > 30:
            return None
        agenda = self.tool_get_todays_agenda()
        return agenda if agenda and agenda != "Calendar not configured." else None

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_get_todays_agenda,
            self.tool_get_upcoming_events,
            self.tool_create_event,
            self.tool_check_free_slots,
        ]

    def get_proactive_checks(self) -> list[dict]:
        return [
            {
                "name": "calendar_morning_brief",
                "interval_seconds": 86400,
                "callback": self._morning_calendar_brief,
                "channel_hint": "telegram",
                "user_id": self._cfg.default_owner_user_id,
            }
        ]


def create_skill():
    return CalendarSyncSkill()

