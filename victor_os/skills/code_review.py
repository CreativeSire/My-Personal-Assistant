"""
Victor-OS Skill: Code Review Agent
Reviews code in a folder or file, flags issues, suggests improvements,
and posts a structured summary to Telegram.
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

logger = get_logger("code_review_skill")

# Simple static analysis patterns
_ISSUES: list[tuple[str, str, str]] = [
    # (pattern, severity, description)
    (r"\beval\s*\(", "CRITICAL", "eval() usage — potential code injection"),
    (r"\bexec\s*\(", "CRITICAL", "exec() usage — potential code injection"),
    (r"\bos\.system\s*\(", "HIGH", "os.system() — prefer subprocess with args list"),
    (r"\bsubprocess\.call\s*\(\s*['\"]", "HIGH", "subprocess with shell string — use list args"),
    (r"\bshell\s*=\s*True", "HIGH", "shell=True — command injection risk"),
    (r"\bpickle\.loads?\s*\(", "HIGH", "pickle.load() — arbitrary code execution risk"),
    (r"\bjoblib\.load\s*\(", "MEDIUM", "joblib.load() — can execute arbitrary code"),
    (r"\bopen\s*\([^)]+\)\s*\.read", "MEDIUM", "open() without context manager — file descriptor leak"),
    (r"password\s*=\s*['\"][^'\"]{3,}", "HIGH", "Hardcoded password literal"),
    (r"api_key\s*=\s*['\"][^'\"]{10,}", "HIGH", "Hardcoded API key literal"),
    (r"secret\s*=\s*['\"][^'\"]{8,}", "HIGH", "Hardcoded secret literal"),
    (r"except\s*:\s*$", "MEDIUM", "Bare except clause — catches all exceptions including SystemExit"),
    (r"except\s+Exception\s*:\s*\n\s*pass", "MEDIUM", "Silent exception swallow — errors hidden"),
    (r"TODO|FIXME|HACK|XXX", "LOW", "Unresolved TODO/FIXME marker"),
    (r"print\s*\(", "LOW", "print() statement — use logger in production code"),
    (r"time\.sleep\s*\(\s*[5-9][0-9]|time\.sleep\s*\(\s*[1-9][0-9]{2,}", "MEDIUM", "Long sleep() — consider async or threading"),
    (r"global\s+\w+", "LOW", "Global variable usage — consider encapsulation"),
    (r"\bmd5\b|\bsha1\b", "MEDIUM", "Weak hash algorithm — use sha256 or better"),
    (r"http://", "LOW", "Plain HTTP URL — prefer HTTPS"),
]

_SUPPORTED_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".cs", ".rb", ".php"}
_IGNORE_DIRS = {"__pycache__", ".git", "node_modules", "venv", ".venv", "dist", "build", ".tox"}


class CodeReviewSkill(Skill):
    """
    Static code review: scans files for common issues and patterns,
    produces a structured report, saves to memory.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="code_review",
            display_name="Code Review Agent",
            version="1.0.0",
            description="Reviews code files for security issues, bugs, and code quality problems.",
            triggers=[
                r"\bcode.*review\b",
                r"\breview.*code\b",
                r"\bcheck.*code\b",
                r"\baudit.*code\b",
                r"\bscan.*file\b",
                r"\bfind.*bugs?\b",
                r"\bsecurity.*check\b",
                r"\bcode.*quality\b",
            ],
            enabled=True,
        )

    # ------------------------------------------------------------------ #
    # Analysis helpers                                                     #
    # ------------------------------------------------------------------ #

    def _analyse_file(self, path: str) -> list[dict]:
        """Run static analysis on a single file. Returns list of issue dicts."""
        findings: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as exc:
            return [{"file": path, "line": 0, "severity": "ERROR", "issue": f"Could not read: {exc}"}]

        for lineno, line in enumerate(lines, 1):
            for pattern, severity, description in _ISSUES:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        "file": path,
                        "line": lineno,
                        "severity": severity,
                        "issue": description,
                        "code": line.rstrip()[:120],
                    })
        return findings

    def _walk_files(self, root: str, max_files: int = 200) -> list[str]:
        """Walk directory and return Python/JS/etc. files."""
        result: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored directories in-place
            dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in _SUPPORTED_EXTS:
                    result.append(os.path.join(dirpath, fname))
                    if len(result) >= max_files:
                        return result
        return result

    def _format_report(
        self, target: str, findings: list[dict], files_scanned: int
    ) -> str:
        critical = [f for f in findings if f["severity"] == "CRITICAL"]
        high = [f for f in findings if f["severity"] == "HIGH"]
        medium = [f for f in findings if f["severity"] == "MEDIUM"]
        low = [f for f in findings if f["severity"] == "LOW"]

        lines = [
            f"Code Review Report: {target}",
            f"Date: {time.strftime('%Y-%m-%d %H:%M')}",
            f"Files scanned: {files_scanned}",
            f"Total issues: {len(findings)} "
            f"(CRITICAL:{len(critical)} HIGH:{len(high)} MEDIUM:{len(medium)} LOW:{len(low)})",
            "=" * 60,
            "",
        ]

        for severity, group in [("CRITICAL", critical), ("HIGH", high), ("MEDIUM", medium), ("LOW", low)]:
            if not group:
                continue
            lines.append(f"── {severity} ({len(group)}) ──")
            for f in group[:20]:  # cap per severity to keep readable
                rel = os.path.relpath(f["file"], os.path.dirname(target)) if os.path.isdir(target) else f["file"]
                lines.append(f"  {rel}:{f['line']} — {f['issue']}")
                if f.get("code"):
                    lines.append(f"    {f['code']}")
            lines.append("")

        if not findings:
            lines.append("No issues found. Code looks clean!")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_review_file(self, file_path: str) -> str:
        """
        Review a single code file and return a structured issue report.
        Args:
            file_path: Absolute or relative path to the file.
        """
        file_path = str(file_path or "").strip()
        if not file_path:
            return "Please provide a file_path."
        if not os.path.isfile(file_path):
            return f"File not found: {file_path}"

        findings = self._analyse_file(file_path)
        report = self._format_report(file_path, findings, files_scanned=1)

        save_memory(
            MemoryRecordInput(
                content=report,
                tags=["code_review", f"file:{os.path.basename(file_path)}"],
                source="code_review",
            )
        )
        return report

    def tool_review_directory(self, directory: str, max_files: int = 100) -> str:
        """
        Review all code files in a directory recursively.
        Args:
            directory: Path to the directory to scan.
            max_files: Maximum number of files to scan (default 100).
        """
        directory = str(directory or "").strip()
        if not directory:
            return "Please provide a directory path."
        if not os.path.isdir(directory):
            return f"Directory not found: {directory}"

        files = self._walk_files(directory, max_files=int(max_files))
        if not files:
            return f"No supported code files found in {directory}."

        all_findings: list[dict] = []
        for fpath in files:
            all_findings.extend(self._analyse_file(fpath))

        report = self._format_report(directory, all_findings, files_scanned=len(files))

        save_memory(
            MemoryRecordInput(
                content=report[:20000],
                tags=["code_review", f"dir:{os.path.basename(directory)}"],
                source="code_review",
            )
        )

        self._de.emit_event(
            self._de.build_event_envelope(
                "action.executed",
                session_id="code_review",
                task_id="",
                actor="code_review",
                payload={
                    "action": "review_directory",
                    "directory": directory,
                    "files": len(files),
                    "issues": len(all_findings),
                },
                risk_score=0.1,
                channel="system",
                source="code_review",
            )
        )

        return report[:4000] + (
            f"\n\n[Full report ({len(report)} chars) saved to memory.]"
            if len(report) > 4000 else ""
        )

    def tool_find_security_issues(self, directory: str) -> str:
        """
        Scan a directory for CRITICAL and HIGH severity security issues only.
        """
        directory = str(directory or "").strip()
        if not os.path.isdir(directory):
            return f"Directory not found: {directory}"
        files = self._walk_files(directory, max_files=200)
        findings = []
        for fpath in files:
            findings.extend([
                f for f in self._analyse_file(fpath)
                if f["severity"] in {"CRITICAL", "HIGH"}
            ])
        if not findings:
            return f"No critical/high security issues found in {directory} ({len(files)} files scanned)."
        report = self._format_report(directory + " [security only]", findings, files_scanned=len(files))
        return report[:4000]

    def tool_search_review_history(self, query: str) -> str:
        """Search past code review reports stored in memory."""
        query = str(query or "").strip()
        if not query:
            return "Please provide a query."
        try:
            from memory_core import recall_memory
            results = recall_memory(query, top_k=5)
            review_results = [
                r for r in results
                if "code_review" in (getattr(r, "tags", None) or [])
            ]
            if not review_results:
                return "No past code reviews matching that query."
            lines = [f"{len(review_results)} past review(s):\n"]
            for i, r in enumerate(review_results[:3], 1):
                content = getattr(r, "content", str(r))
                lines.append(f"[{i}] {content[:400].strip()}\n")
            return "\n".join(lines)
        except Exception as exc:
            return f"Search failed: {exc}"

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_review_file,
            self.tool_review_directory,
            self.tool_find_security_issues,
            self.tool_search_review_history,
        ]


def create_skill():
    return CodeReviewSkill()
