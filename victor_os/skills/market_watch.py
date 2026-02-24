import json
import urllib.parse
import urllib.request
import urllib.error
from typing import Callable

from skill_base import Skill, SkillManifest
from sdk import get_config, emit_event


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

    def _fetch_crypto_prices(self, symbols: list[str]) -> dict[str, float]:
        ids = [SYMBOL_MAP.get(s.upper()) for s in symbols if SYMBOL_MAP.get(s.upper())]
        if not ids:
            return {}
        query = urllib.parse.urlencode({"ids": ",".join(ids), "vs_currencies": "usd"})
        url = f"https://api.coingecko.com/api/v3/simple/price?{query}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status != 200:
                    logger.warning(f"CoinGecko returned HTTP {response.status}")
                    return {}
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            logger.warning(f"CoinGecko fetch failed: {exc}")
            return {}
        out: dict[str, float] = {}
        for sym in symbols:
            coin_id = SYMBOL_MAP.get(sym.upper())
            if coin_id and coin_id in payload and "usd" in payload[coin_id]:
                out[sym.upper()] = float(payload[coin_id]["usd"])
        return out

    def _fetch_forex_prices(self, pairs: list[str]) -> dict[str, float]:
        # Uses exchangerate.host (no key) as a best-effort FX source.
        out: dict[str, float] = {}
        for pair in pairs:
            base, quote = pair.split("/", 1)
            base = base.strip().upper()
            quote = quote.strip().upper()
            if not base or not quote:
                continue
            url = f"https://api.exchangerate.host/latest?base={urllib.parse.quote(base)}&symbols={urllib.parse.quote(quote)}"
            try:
                with urllib.request.urlopen(url, timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                rate = (payload.get("rates") or {}).get(quote)
                if rate is not None:
                    out[f"{base}/{quote}"] = float(rate)
            except Exception:
                continue
        return out

    def _fetch_stock_prices(self, tickers: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        try:
            import yfinance as yf  # type: ignore
        except Exception:
            return out
        for sym in tickers:
            s = sym.strip().upper()
            if not s:
                continue
            try:
                t = yf.Ticker(s)
                info = getattr(t, "fast_info", None) or {}
                price = info.get("last_price") or info.get("lastPrice") or info.get("regularMarketPrice")
                if price is None:
                    # Fallback (slower / sometimes None)
                    price = (t.info or {}).get("regularMarketPrice")
                if price is not None:
                    out[s] = float(price)
            except Exception:
                continue
        return out

    def _fetch_prices(self) -> dict[str, float]:
        raw = list(self._cfg.market_watch_symbols or ("BTC", "ETH"))
        symbols = [str(x).strip() for x in raw if str(x).strip()]
        crypto = [s for s in symbols if s.upper() in SYMBOL_MAP]
        forex = [s for s in symbols if "/" in s and len(s.split("/", 1)[0]) <= 6]
        stocks = [s for s in symbols if s.upper() not in SYMBOL_MAP and "/" not in s]

        out: dict[str, float] = {}
        out.update(self._fetch_crypto_prices(crypto))
        out.update(self._fetch_stock_prices(stocks))
        out.update(self._fetch_forex_prices(forex))
        return out

    def tool_market_snapshot(self) -> str:
        prices = self._fetch_prices()
        if not prices:
            return "Market snapshot unavailable."
        lines = []
        for sym, price in sorted(prices.items()):
            if "/" in sym:
                lines.append(f"- {sym}: {price:,.4f}")
            else:
                lines.append(f"- {sym}: ${price:,.2f}")
        try:
            emit_event(
                event_type="skill.market_watch.snapshot",
                actor="market_watch",
                payload={"symbols": list(sorted(prices.keys())), "count": len(prices)},
                channel="system",
                risk_score=0.1,
            )
        except Exception:
            pass
        return "Market Snapshot\n" + "\n".join(lines)

    def get_tools(self) -> list[Callable]:
        return [self.tool_market_snapshot]

    def get_proactive_checks(self) -> list[dict]:
        def _check():
            prices = self._fetch_prices()
            if not prices:
                return None
            alerts = []
            threshold = float(getattr(self._cfg, "market_watch_move_threshold", 1.5) or 1.5)
            for sym, price in prices.items():
                prev = self._last_prices.get(sym)
                self._last_prices[sym] = price
                if not prev or prev <= 0:
                    continue
                move = ((price - prev) / prev) * 100
                if abs(move) >= threshold:
                    if "/" in sym:
                        alerts.append(f"{sym} moved {move:.2f}% to {price:,.4f}")
                    else:
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
                "user_id": self._cfg.default_owner_user_id,
            }
        ]


def create_skill():
    return MarketWatchSkill()
