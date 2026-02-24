import os
import threading
import time
import tempfile
import zipfile
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("file_watcher_skill")


class FileWatcherSkill(Skill):
    """
    Drop-to-process inbox watcher.

    Note: This is feature-flagged via FILE_WATCHER_ENABLED and is designed to be non-blocking.
    Notification fanout is handled by existing proactive/task pipelines; this watcher primarily
    enqueues jobs and writes telemetry.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        self._observer = None
        self._thread: threading.Thread | None = None
        self._running = False

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="file_watcher",
            display_name="File Watcher",
            version="1.0.0",
            description="Watches an inbox folder and routes files into Victor pipelines.",
            triggers=[r"\binbox\b", r"\bwatch folder\b", r"\bfile watcher\b"],
            enabled=True,
        )

    def on_load(self) -> None:
        if not bool(self._cfg.file_watcher_enabled):
            return
        inbox = str(self._cfg.file_watcher_inbox or "").strip()
        if not inbox:
            return
        os.makedirs(inbox, exist_ok=True)
        try:
            from watchdog.observers import Observer  # type: ignore
            from watchdog.events import FileSystemEventHandler  # type: ignore
        except Exception as exc:
            logger.warning(f"watchdog not installed; file watcher disabled: {exc}")
            return

        skill = self

        class _Handler(FileSystemEventHandler):
            def on_created(self, event):  # type: ignore[no-untyped-def]
                if getattr(event, "is_directory", False):
                    return
                path = str(getattr(event, "src_path", "") or "")
                if not path:
                    return
                # Debounce for partial writes.
                time.sleep(2.0)
                try:
                    skill._handle_new_file(path)
                except Exception as e:
                    logger.warning(f"File watcher handle failed: {e}")

        self._observer = Observer()
        self._observer.schedule(_Handler(), inbox, recursive=False)
        self._observer.start()
        logger.info(f"File watcher started on: {inbox}")

    def on_unload(self) -> None:
        try:
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=5)
        except Exception:
            pass
        self._observer = None

    def _emit(self, event_type: str, payload: dict) -> None:
        try:
            self._de.emit_event(
                self._de.build_event_envelope(
                    event_type,
                    session_id="file_watcher",
                    task_id="",
                    actor="file_watcher",
                    payload=payload,
                    risk_score=0.2,
                    channel="system",
                    source="file_watcher",
                )
            )
        except Exception:
            pass

    def _handle_new_file(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        self._emit("intent.received", {"inbox_file": path, "ext": ext})

        if ext in {"pdf", "jpg", "jpeg", "png", "tif", "tiff", "bmp", "webp", "heic"}:
            try:
                from invoice_pipeline import enqueue_invoice_job

                task_id = enqueue_invoice_job(path, channel="system", user_id=self._cfg.default_owner_user_id)
                self._emit("plan.generated", {"task_type": "invoice_job", "task_id": task_id, "path": path})
            except Exception as e:
                self._emit("action.blocked", {"error": str(e), "path": path})
            return

        if ext in {"txt", "md"}:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(20000)
                from memory_core import save_memory, MemoryRecordInput

                save_memory(
                    MemoryRecordInput(
                        content=text,
                        tags=["file_watcher", "project_fact"],
                        source="file_watcher",
                    )
                )
                self._emit("action.executed", {"action": "save_memory", "path": path})
            except Exception as e:
                self._emit("action.blocked", {"error": str(e), "path": path})
            return

        if ext == "zip":
            try:
                tmp_dir = tempfile.mkdtemp(prefix="victor_inbox_")
                with zipfile.ZipFile(path, "r") as zf:
                    # Guard against path traversal: only extract safe member names.
                    for member in zf.infolist():
                        member_name = member.filename
                        # Reject absolute paths and any path components that traverse up.
                        if os.path.isabs(member_name) or ".." in member_name.split("/"):
                            logger.warning(f"ZIP path traversal attempt blocked: {member_name}")
                            continue
                        # Only extract files (not directories), flat into tmp_dir.
                        safe_dest = os.path.realpath(os.path.join(tmp_dir, os.path.basename(member_name)))
                        if not safe_dest.startswith(os.path.realpath(tmp_dir)):
                            logger.warning(f"ZIP extraction target outside sandbox, skipped: {member_name}")
                            continue
                        if not member.is_dir():
                            with zf.open(member) as src, open(safe_dest, "wb") as dst:
                                dst.write(src.read())
                # For each extracted file, route again.
                for name in os.listdir(tmp_dir):
                    fp = os.path.join(tmp_dir, name)
                    if os.path.isfile(fp):
                        self._handle_new_file(fp)
                self._emit("action.executed", {"action": "zip_routed", "path": path})
            except Exception as e:
                self._emit("action.blocked", {"error": str(e), "path": path})
            return

        if ext in {"mp3", "wav", "ogg", "m4a"}:
            try:
                from tools import transcribe_audio_file
                from memory_core import save_memory, MemoryRecordInput

                text = transcribe_audio_file(path)
                save_memory(
                    MemoryRecordInput(
                        content=text,
                        tags=["file_watcher", "transcription"],
                        source="file_watcher",
                    )
                )
                self._emit("action.executed", {"action": "transcribe_audio_file", "path": path})
            except Exception as e:
                self._emit("action.blocked", {"error": str(e), "path": path})
            return

        self._emit("action.executed", {"action": "unknown_file_type", "path": path})

    def tool_list_inbox(self) -> str:
        inbox = str(self._cfg.file_watcher_inbox or "").strip()
        if not inbox or not os.path.isdir(inbox):
            return "Inbox not configured."
        items = [x for x in os.listdir(inbox) if os.path.isfile(os.path.join(inbox, x))]
        if not items:
            return "Inbox empty."
        return "Inbox files:\n" + "\n".join(f"- {name}" for name in sorted(items)[:200])

    def tool_clear_file_inbox(self) -> str:
        """Clear all files from the file watcher inbox directory."""
        inbox = str(self._cfg.file_watcher_inbox or "").strip()
        if not inbox or not os.path.isdir(inbox):
            return "Inbox not configured."
        removed = 0
        for name in os.listdir(inbox):
            path = os.path.join(inbox, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    removed += 1
                except Exception:
                    continue
        return f"Cleared file inbox: removed {removed} file(s)."

    def get_tools(self) -> list[Callable]:
        return [self.tool_list_inbox, self.tool_clear_file_inbox]


def create_skill():
    return FileWatcherSkill()

