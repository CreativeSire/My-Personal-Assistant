"""
Victor-OS Skill: Document Brain
Ingests PDF, Word, and text files — extracts key facts into memory,
and answers conversational questions about ingested content.
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from memory_core import MemoryRecordInput, save_memory, recall_memory
from skill_base import Skill, SkillManifest

logger = get_logger("document_brain_skill")

_SUPPORTED_EXTS = {"pdf", "docx", "doc", "txt", "md", "rtf", "pptx"}


class DocumentBrainSkill(Skill):
    """
    Ingests documents and answers questions about them conversationally.
    Stores extracted text in memory_core under tag 'document_brain'.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        # Track ingested hashes to avoid duplicate ingestion
        self._ingested: set[str] = set()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="document_brain",
            display_name="Document Brain",
            version="1.0.0",
            description="Ingests PDF/Word/text files and answers questions about their content.",
            triggers=[
                r"\bsummarise.*document\b",
                r"\bsummarize.*document\b",
                r"\bread.*file\b",
                r"\bingest.*document\b",
                r"\bwhat.*does.*document.*say\b",
                r"\bfind.*in.*document\b",
                r"\bextract.*from.*file\b",
                r"\banalyse.*document\b",
                r"\banalyze.*document\b",
            ],
            enabled=True,
        )

    # ------------------------------------------------------------------ #
    # Extraction helpers                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_pdf(path: str) -> str:
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(path)
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages)
        except ImportError:
            pass
        try:
            import pdfminer.high_level as pdfminer  # type: ignore
            return pdfminer.extract_text(path)
        except Exception as exc:
            return f"[PDF extraction failed: {exc}]"

    @staticmethod
    def _extract_docx(path: str) -> str:
        try:
            import docx  # type: ignore
            doc = docx.Document(path)
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as exc:
            return f"[DOCX extraction failed: {exc}]"

    @staticmethod
    def _extract_text(path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(100_000)

    def _extract(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext == "pdf":
            return self._extract_pdf(path)
        if ext in {"docx", "doc"}:
            return self._extract_docx(path)
        return self._extract_text(path)

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_ingest_document(self, path: str) -> str:
        """
        Ingest a document at the given file path into Victor's memory.
        Supported formats: pdf, docx, txt, md.
        """
        path = str(path or "").strip()
        if not path:
            return "Please provide a file path."
        if not os.path.isfile(path):
            return f"File not found: {path}"
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext not in _SUPPORTED_EXTS:
            return f"Unsupported file type: .{ext}. Supported: {', '.join(sorted(_SUPPORTED_EXTS))}"

        # Dedup by content hash
        with open(path, "rb") as f:
            file_hash = hashlib.sha256(f.read(65536)).hexdigest()[:16]
        if file_hash in self._ingested:
            return f"Document already ingested (hash={file_hash})."

        try:
            text = self._extract(path)
        except Exception as exc:
            return f"Extraction failed: {exc}"

        if not text.strip():
            return "Document appears to be empty or unreadable."

        # Store in memory in chunks of 4000 chars to stay within embedding limits
        chunk_size = 4000
        chunks = [text[i:i + chunk_size] for i in range(0, min(len(text), 80000), chunk_size)]
        filename = os.path.basename(path)

        for i, chunk in enumerate(chunks):
            save_memory(
                MemoryRecordInput(
                    content=f"[Document: {filename} | chunk {i + 1}/{len(chunks)}]\n{chunk}",
                    tags=["document_brain", f"doc:{filename}", ext],
                    source="document_brain",
                )
            )

        self._ingested.add(file_hash)
        self._de.emit_event(
            self._de.build_event_envelope(
                "action.executed",
                session_id="document_brain",
                task_id="",
                actor="document_brain",
                payload={"action": "ingest", "file": filename, "chunks": len(chunks)},
                risk_score=0.1,
                channel="system",
                source="document_brain",
            )
        )
        return f"Ingested '{filename}' — {len(chunks)} chunk(s) stored in memory ({len(text):,} chars)."

    def tool_query_documents(self, query: str) -> str:
        """
        Search ingested documents for information matching the query.
        Returns the most relevant passages found in memory.
        """
        query = str(query or "").strip()
        if not query:
            return "Please provide a search query."
        try:
            results = recall_memory(query, top_k=5)
        except Exception as exc:
            return f"Memory search failed: {exc}"
        if not results:
            return "No matching content found in ingested documents. Try ingesting a document first."

        # Filter to document_brain memories only
        doc_results = [r for r in results if "document_brain" in (getattr(r, "tags", None) or [])]
        if not doc_results:
            doc_results = results  # fallback to all if none tagged

        lines = [f"Found {len(doc_results)} relevant passage(s):\n"]
        for i, r in enumerate(doc_results[:5], 1):
            content = getattr(r, "content", str(r))
            lines.append(f"[{i}] {content[:400].strip()}\n")
        return "\n".join(lines)

    def tool_list_ingested(self) -> str:
        """List all documents currently tracked as ingested this session."""
        if not self._ingested:
            return "No documents ingested this session."
        return f"Ingested hashes this session: {len(self._ingested)}\n" + "\n".join(
            f"- {h}" for h in sorted(self._ingested)
        )

    def tool_summarise_document(self, path: str) -> str:
        """Extract and return the first ~800 characters of a document as a quick summary."""
        path = str(path or "").strip()
        if not os.path.isfile(path):
            return f"File not found: {path}"
        try:
            text = self._extract(path)
        except Exception as exc:
            return f"Extraction failed: {exc}"
        preview = text.strip()[:800]
        return f"Summary of '{os.path.basename(path)}':\n\n{preview}..."

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_ingest_document,
            self.tool_query_documents,
            self.tool_list_ingested,
            self.tool_summarise_document,
        ]


def create_skill():
    return DocumentBrainSkill()
