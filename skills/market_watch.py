import json
import urllib.parse
import urllib.request
from typing import Callable

from config import get_config
from skill_base import Skill, SkillManifest


SYMBOL_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
}


class MarketWatchSkill(Skill):
    def __init__(self):
        self._cfg = get_config()
        self._last_prices: dict[str, float] = {}

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="market_watch",
            display_name="Market Watch",
            version="1.0.0",
            description="Tracks market symbols and emits threshold movement alerts.",
            triggers=[r"\bmarket watch\b", r"\bprice alert\b", r"\bbitcoin price\b", r"\bethereum price\b"],
        )

    def _fetch_prices(self) -> dict[str, float]:
        symbols = list(self._cfg.market_watch_symbols or ("BTC", "ETH"))
        ids = [SYMBOL_MAP.get(s.upper()) for s in symbols if SYMBOL_MAP.get(s.upper())]
        if not ids:
            ids = ["bitcoin", "ethereum"]
        query = urllib.parse.urlencode(
            {
                "ids": ",".join(ids),
                "vs_currencies": "usd",
            }
        )
        url = f"https://api.coingecko.com/api/v3/simple/price?{query}"
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        out = {}
        for sym, coin_id in SYMBOL_MAP.items():
            if coin_id in payload and "usd" in payload[coin_id]:
                out[sym] = float(payload[coin_id]["usd"])
        return out

    def tool_market_snapshot(self) -> str:
        prices = self._fetch_prices()
        if not prices:
            return "Market snapshot unavailable."
        lines = [f"- {sym}: ${price:,.2f}" for sym, price in sorted(prices.items())]
        return "Market Snapshot (USD)\n" + "\n".join(lines)

    def get_tools(self) -> list[Callable]:
        return [self.tool_market_snapshot]

    def get_proactive_checks(self) -> list[dict]:
        def _check():
            prices = self._fetch_prices()
            if not prices:
                return None
            alerts = []
            for sym, price in prices.items():
                prev = self._last_prices.get(sym)
                self._last_prices[sym] = price
                if not prev or prev <= 0:
                    continue
                move = ((price - prev) / prev) * 100
                if abs(move) >= 1.5:
                    alerts.append(f"{sym} moved {move:.2f}% to ${price:,.2f}")
            if alerts:
                return "Market Watch Alert\n" + "\n".join(f"- {line}" for line in alerts)
            return None

        return [
            {
                "name": "market_watch_moves",
                "interval_seconds": 300,
                "callback": _check,
                "channel_hint": "telegram",
                "user_id": "ceejay",
            }
        ]


def create_skill():
    return MarketWatchSkill()
