"""
Victor-OS Skill: Research Agent
Autonomous topic research — cross-references multiple sources,
structures findings, and saves a comprehensive report to memory.
"""
from __future__ import annotations

import os
import time
import urllib.parse
import urllib.request
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from memory_core import MemoryRecordInput, save_memory
from skill_base import Skill, SkillManifest

logger = get_logger("research_agent_skill")

_USER_AGENT = "Victor-OS/1.0 (personal research agent)"


class ResearchAgentSkill(Skill):
    """
    Autonomous research: fetches web content, cross-references sources,
    generates a structured report, and stores it in memory.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="research_agent",
            display_name="Research Agent",
            version="1.0.0",
            description="Autonomous topic research — fetches, cross-references, and structures findings.",
            triggers=[
                r"\bresearch\b",
                r"\bfind.*information.*on\b",
                r"\binvestigate\b",
                r"\blook.*up\b",
                r"\bwhat.*is\b.*\?",
                r"\bwho.*is\b",
                r"\btell.*me.*about\b",
                r"\bdeep.*dive\b",
                r"\banalyse\b",
                r"\banalyze\b",
            ],
            enabled=True,
        )

    # ------------------------------------------------------------------ #
    # Web helpers                                                          #
    # ------------------------------------------------------------------ #

    def _fetch_url(self, url: str, max_bytes: int = 65536) -> str:
        """Fetch raw text content from a URL."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read(max_bytes)
                charset = resp.headers.get_content_charset("utf-8") or "utf-8"
                return raw.decode(charset, errors="replace")
        except Exception as exc:
            return f"[Fetch failed: {exc}]"

    @staticmethod
    def _strip_html(html: str) -> str:
        """Very simple HTML → plain text stripper."""
        import re
        text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{3,}", "\n\n", text)
        return text.strip()

    def _search_ddg(self, query: str, num_results: int = 5) -> list[dict]:
        """Search DuckDuckGo Instant Answer API — no API key required."""
        results = []
        try:
            params = urllib.parse.urlencode({"q": query, "format": "json", "no_redirect": "1"})
            url = f"https://api.duckduckgo.com/?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json
                data = json.loads(resp.read().decode("utf-8"))

            # Instant answer
            if data.get("AbstractText"):
                results.append({
                    "title": data.get("Heading", query),
                    "url": data.get("AbstractURL", ""),
                    "snippet": data["AbstractText"][:500],
                    "source": "ddg_abstract",
                })

            # Related topics
            for topic in (data.get("RelatedTopics") or [])[:num_results]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append({
                        "title": topic.get("Text", "")[:80],
                        "url": topic.get("FirstURL", ""),
                        "snippet": topic.get("Text", "")[:300],
                        "source": "ddg_related",
                    })
        except Exception as exc:
            logger.debug(f"DDG search failed: {exc}")
        return results[:num_results]

    def _search_wikipedia(self, query: str) -> dict | None:
        """Fetch Wikipedia summary for a query."""
        try:
            params = urllib.parse.urlencode({
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": "1",
            })
            url = f"https://en.wikipedia.org/w/api.php?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json
                data = json.loads(resp.read().decode("utf-8"))
            hits = (data.get("query") or {}).get("search", [])
            if not hits:
                return None
            title = hits[0].get("title", "")
            snippet = hits[0].get("snippet", "")
            # Strip HTML tags from snippet
            import re
            snippet = re.sub(r"<[^>]+>", "", snippet)
            # Get full summary
            summary_url = (
                f"https://en.wikipedia.org/api/rest_v1/page/summary/"
                + urllib.parse.quote(title.replace(" ", "_"))
            )
            req2 = urllib.request.Request(summary_url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                summary_data = json.loads(resp2.read().decode("utf-8"))
            return {
                "title": title,
                "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
                "snippet": summary_data.get("extract", snippet)[:800],
                "source": "wikipedia",
            }
        except Exception as exc:
            logger.debug(f"Wikipedia fetch failed: {exc}")
            return None

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_research_topic(self, topic: str, depth: str = "standard") -> str:
        """
        Research a topic autonomously across multiple sources.
        Args:
            topic: The subject to research.
            depth: 'quick' (Wikipedia only), 'standard' (DDG + Wikipedia), 'deep' (+ URL fetch).
        Returns:
            A structured research report saved to memory.
        """
        topic = str(topic or "").strip()
        if not topic:
            return "Please provide a topic to research."
        depth = str(depth or "standard").lower()

        logger.info(f"Researching topic: {topic} (depth={depth})")
        sources: list[dict] = []

        # Wikipedia
        wiki = self._search_wikipedia(topic)
        if wiki:
            sources.append(wiki)

        # DuckDuckGo (standard + deep)
        if depth in {"standard", "deep"}:
            ddg_results = self._search_ddg(topic, num_results=4)
            sources.extend(ddg_results)

        # Deep: also fetch the top DDG URL
        if depth == "deep":
            for s in sources[:2]:
                if s.get("url") and s["url"].startswith("http"):
                    try:
                        raw_html = self._fetch_url(s["url"])
                        page_text = self._strip_html(raw_html)[:3000]
                        s["full_text"] = page_text
                    except Exception:
                        pass

        if not sources:
            return f"No research results found for '{topic}'. Try a different search term."

        # Build report
        report_lines = [
            f"Research Report: {topic}",
            f"Date: {time.strftime('%Y-%m-%d %H:%M')}",
            f"Depth: {depth}",
            f"Sources: {len(sources)}",
            "=" * 60,
            "",
        ]

        for i, src in enumerate(sources, 1):
            report_lines.append(f"[Source {i}] {src.get('title', 'Unknown')} ({src.get('source', '')})")
            if src.get("url"):
                report_lines.append(f"URL: {src['url']}")
            report_lines.append(src.get("snippet", "")[:600])
            if src.get("full_text"):
                report_lines.append("\nPage content excerpt:")
                report_lines.append(src["full_text"][:1000])
            report_lines.append("")

        report = "\n".join(report_lines)

        # Save to memory
        save_memory(
            MemoryRecordInput(
                content=report[:20000],
                tags=["research_agent", f"research:{topic[:40]}"],
                source="research_agent",
            )
        )

        self._de.emit_event(
            self._de.build_event_envelope(
                "action.executed",
                session_id="research_agent",
                task_id="",
                actor="research_agent",
                payload={"action": "research", "topic": topic, "sources": len(sources)},
                risk_score=0.1,
                channel="system",
                source="research_agent",
            )
        )

        return report[:4000] + (f"\n\n[Report truncated. Full report ({len(report)} chars) saved to memory.]"
                                if len(report) > 4000 else "")

    def tool_fetch_and_summarise_url(self, url: str) -> str:
        """
        Fetch a URL and return a text summary of its content.
        Args:
            url: The URL to fetch and summarise.
        """
        url = str(url or "").strip()
        if not url.startswith("http"):
            return "Please provide a valid URL starting with http:// or https://"
        raw = self._fetch_url(url)
        if raw.startswith("[Fetch failed"):
            return raw
        text = self._strip_html(raw)[:5000]
        save_memory(
            MemoryRecordInput(
                content=f"[URL Content: {url}]\n{text}",
                tags=["research_agent", "url_fetch"],
                source="research_agent",
            )
        )
        return f"Content from {url}:\n\n{text[:2000]}" + ("\n...[truncated]" if len(text) > 2000 else "")

    def tool_quick_fact(self, query: str) -> str:
        """
        Get a quick fact or definition using Wikipedia's instant answer.
        """
        query = str(query or "").strip()
        if not query:
            return "Please provide a query."
        wiki = self._search_wikipedia(query)
        if wiki:
            return f"{wiki['title']}:\n\n{wiki['snippet']}\n\nSource: {wiki['url']}"
        ddg = self._search_ddg(query, num_results=1)
        if ddg:
            return ddg[0].get("snippet", "No results found.")
        return f"No quick facts found for '{query}'."

    def tool_search_past_research(self, query: str) -> str:
        """Search previous research reports stored in memory."""
        query = str(query or "").strip()
        if not query:
            return "Please provide a search query."
        try:
            from memory_core import recall_memory
            results = recall_memory(query, top_k=5)
            research_results = [
                r for r in results
                if "research_agent" in (getattr(r, "tags", None) or [])
            ]
            if not research_results:
                return "No past research found matching that query."
            lines = [f"{len(research_results)} past research result(s):\n"]
            for i, r in enumerate(research_results[:3], 1):
                content = getattr(r, "content", str(r))
                lines.append(f"[{i}] {content[:400].strip()}\n")
            return "\n".join(lines)
        except Exception as exc:
            return f"Memory search failed: {exc}"

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_research_topic,
            self.tool_fetch_and_summarise_url,
            self.tool_quick_fact,
            self.tool_search_past_research,
        ]


def create_skill():
    return ResearchAgentSkill()
