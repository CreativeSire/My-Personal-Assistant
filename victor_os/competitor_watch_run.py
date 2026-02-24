from __future__ import annotations

import os
import re
import time
import urllib.request
from pathlib import Path


OUT = Path(__file__).resolve().parent.parent / "docs" / "reports" / "competitor_watch.md"


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "VictorBot/1.0"}, method="GET")
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_titles(xml_or_html: str) -> list[str]:
    # Very simple: RSS/Atom <title> tags; skip the first feed title if present.
    titles = re.findall(r"<title[^>]*>(.*?)</title>", xml_or_html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = []
    for t in titles:
        t = re.sub(r"<.*?>", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        if t and t.lower() not in {"rss", "atom"}:
            cleaned.append(t)
    # dedupe preserve order
    out = []
    seen = set()
    for t in cleaned:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def main() -> int:
    feeds = [x.strip() for x in os.getenv("COMPETITOR_FEEDS", "").split(",") if x.strip()]
    if not feeds:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            "# Competitor Watch\n\nNo feeds configured. Set COMPETITOR_FEEDS to comma-separated RSS/Atom URLs.\n",
            encoding="utf-8",
        )
        print(f"wrote={OUT}")
        return 0

    items = []
    for url in feeds[:10]:
        try:
            raw = _fetch(url)
            titles = _extract_titles(raw)[:10]
            items.append((url, titles))
        except Exception as exc:
            items.append((url, [f"ERROR: {exc}"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# Competitor Watch\n\n")
        f.write(f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        for url, titles in items:
            f.write(f"## {url}\n")
            for t in titles:
                f.write(f"- {t}\n")
            f.write("\n")

    print(f"wrote={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

