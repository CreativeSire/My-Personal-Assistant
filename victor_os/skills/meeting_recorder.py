"""
Victor-OS Skill: Meeting Recorder
Transcribes audio recordings of meetings, extracts action items,
adds them to goal tracker, and sends summary to Telegram.
"""
from __future__ import annotations

import os
import re
import time
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from memory_core import MemoryRecordInput, save_memory
from skill_base import Skill, SkillManifest

logger = get_logger("meeting_recorder_skill")

_ACTION_PATTERNS = re.compile(
    r"(?:action item|todo|to[\-\s]do|follow[\-\s]up|i will|we will|you will|"
    r"assigned to|responsible for|next step)[:\s]+([^\.\n]{10,120})",
    re.IGNORECASE,
)
_DECISION_PATTERNS = re.compile(
    r"(?:decided|agreed|confirmed|resolved|concluded)[:\s]+([^\.\n]{10,120})",
    re.IGNORECASE,
)


class MeetingRecorderSkill(Skill):
    """
    Transcribes meeting audio, extracts action items and decisions,
    saves to memory, and optionally adds to goal tracker.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="meeting_recorder",
            display_name="Meeting Recorder",
            version="1.0.0",
            description="Transcribes meetings, extracts action items and decisions, saves to memory.",
            triggers=[
                r"\bmeeting.*notes\b",
                r"\brecord.*meeting\b",
                r"\btranscribe.*meeting\b",
                r"\bmeeting.*summary\b",
                r"\baction.*items\b",
                r"\bwhat.*decided\b",
                r"\bmeeting.*recap\b",
            ],
            enabled=True,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _transcribe(self, audio_path: str) -> str:
        """Transcribe audio using Whisper (primary) or Google STT (fallback)."""
        try:
            import whisper  # type: ignore
            model = whisper.load_model("base")
            result = model.transcribe(audio_path)
            return result["text"].strip()
        except ImportError:
            pass
        # Fallback to tools.py transcriber
        try:
            from tools import transcribe_audio_file
            return transcribe_audio_file(audio_path)
        except Exception as exc:
            return f"[Transcription failed: {exc}]"

    @staticmethod
    def _extract_action_items(text: str) -> list[str]:
        matches = _ACTION_PATTERNS.findall(text)
        return [m.strip() for m in matches if len(m.strip()) > 5]

    @staticmethod
    def _extract_decisions(text: str) -> list[str]:
        matches = _DECISION_PATTERNS.findall(text)
        return [m.strip() for m in matches if len(m.strip()) > 5]

    def _format_meeting_summary(
        self,
        title: str,
        transcript: str,
        action_items: list[str],
        decisions: list[str],
    ) -> str:
        lines = [f"Meeting: {title}", f"Date: {time.strftime('%Y-%m-%d %H:%M')}", ""]
        if decisions:
            lines.append("Decisions made:")
            for d in decisions:
                lines.append(f"  • {d}")
            lines.append("")
        if action_items:
            lines.append("Action items:")
            for a in action_items:
                lines.append(f"  ☐ {a}")
            lines.append("")
        lines.append(f"Transcript ({len(transcript)} chars):")
        lines.append(transcript[:1500] + ("..." if len(transcript) > 1500 else ""))
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_transcribe_meeting(self, audio_path: str, title: str = "") -> str:
        """
        Transcribe a meeting audio file and extract action items and decisions.
        Args:
            audio_path: Path to audio file (mp3, wav, m4a, ogg).
            title: Optional meeting title for reference.
        """
        audio_path = str(audio_path or "").strip()
        if not audio_path:
            return "Please provide audio_path."
        if not os.path.isfile(audio_path):
            return f"File not found: {audio_path}"

        title = title.strip() or f"Meeting_{time.strftime('%Y%m%d_%H%M')}"

        logger.info(f"Transcribing meeting: {audio_path}")
        transcript = self._transcribe(audio_path)

        if transcript.startswith("[Transcription failed"):
            return transcript

        action_items = self._extract_action_items(transcript)
        decisions = self._extract_decisions(transcript)
        summary = self._format_meeting_summary(title, transcript, action_items, decisions)

        # Save full transcript to memory
        save_memory(
            MemoryRecordInput(
                content=f"[Meeting Transcript: {title}]\n{transcript[:20000]}",
                tags=["meeting_recorder", "transcript", title],
                source="meeting_recorder",
            )
        )

        # Save action items to memory with dedicated tag
        if action_items:
            save_memory(
                MemoryRecordInput(
                    content=f"[Meeting Action Items: {title}]\n" + "\n".join(f"• {a}" for a in action_items),
                    tags=["meeting_recorder", "action_items", title],
                    source="meeting_recorder",
                )
            )

        self._de.emit_event(
            self._de.build_event_envelope(
                "action.executed",
                session_id="meeting_recorder",
                task_id="",
                actor="meeting_recorder",
                payload={
                    "action": "transcribe_meeting",
                    "title": title,
                    "action_items": len(action_items),
                    "decisions": len(decisions),
                },
                risk_score=0.1,
                channel="system",
                source="meeting_recorder",
            )
        )

        return summary

    def tool_process_text_notes(self, notes: str, title: str = "") -> str:
        """
        Process raw text meeting notes (not audio) — extract action items and decisions.
        Args:
            notes: Raw meeting notes text.
            title: Optional meeting title.
        """
        notes = str(notes or "").strip()
        if not notes:
            return "Please provide meeting notes text."

        title = title.strip() or f"Notes_{time.strftime('%Y%m%d_%H%M')}"
        action_items = self._extract_action_items(notes)
        decisions = self._extract_decisions(notes)
        summary = self._format_meeting_summary(title, notes, action_items, decisions)

        save_memory(
            MemoryRecordInput(
                content=f"[Meeting Notes: {title}]\n{notes[:20000]}",
                tags=["meeting_recorder", "notes", title],
                source="meeting_recorder",
            )
        )

        return summary

    def tool_push_action_items_to_goals(self, meeting_title: str) -> str:
        """
        Push extracted action items from a meeting into the goal tracker.
        Args:
            meeting_title: Title used when the meeting was recorded.
        """
        try:
            from memory_core import recall_memory
            results = recall_memory(f"Meeting Action Items: {meeting_title}", top_k=3)
            if not results:
                return f"No action items found for meeting: {meeting_title}"

            from goal_tracker import get_goal_tracker
            tracker = get_goal_tracker()

            added = 0
            for r in results:
                content = getattr(r, "content", "")
                if "action_items" not in (getattr(r, "tags", None) or []):
                    continue
                items_text = content.split("\n")[1:]  # skip header line
                for item in items_text:
                    item = item.strip().lstrip("•").strip()
                    if len(item) > 5:
                        try:
                            tracker.create_goal(
                                title=item[:100],
                                description=f"From meeting: {meeting_title}",
                                source="meeting_recorder",
                            )
                            added += 1
                        except Exception:
                            pass

            return f"Added {added} action item(s) to goal tracker from '{meeting_title}'."
        except Exception as exc:
            return f"Failed to push to goals: {exc}"

    def tool_search_meeting_history(self, query: str) -> str:
        """Search past meeting transcripts and notes for a topic."""
        query = str(query or "").strip()
        if not query:
            return "Please provide a search query."
        try:
            from memory_core import recall_memory
            results = recall_memory(query, top_k=5)
            meeting_results = [
                r for r in results
                if "meeting_recorder" in (getattr(r, "tags", None) or [])
            ]
            if not meeting_results:
                return "No matching meeting history found."
            lines = [f"{len(meeting_results)} result(s) from meeting history:\n"]
            for i, r in enumerate(meeting_results[:4], 1):
                content = getattr(r, "content", str(r))
                lines.append(f"[{i}] {content[:350].strip()}\n")
            return "\n".join(lines)
        except Exception as exc:
            return f"Search failed: {exc}"

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_transcribe_meeting,
            self.tool_process_text_notes,
            self.tool_push_action_items_to_goals,
            self.tool_search_meeting_history,
        ]


def create_skill():
    return MeetingRecorderSkill()
