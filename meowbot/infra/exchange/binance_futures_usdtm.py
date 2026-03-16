from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

import requests

from meowbot.core.domain.types import Bar


@dataclass(frozen=True)
class BinanceFuturesConfig:
    base_url: str = "https://fapi.binance.com"   # USDT-M Futures
    timeout_s: float = 10.0


class BinanceFuturesMarketData:
    """
    Реалізує порт meowbot.core.ports.exchange_market_data.ExchangeMarketData
    Поки тільки public market-data: ping, klines.
    """
    def __init__(self, cfg: BinanceFuturesConfig | None = None):
        self.cfg = cfg or BinanceFuturesConfig()
        self.session = requests.Session()

        # ключі для klines не потрібні, але хай будуть на майбутнє
        self.api_key = os.getenv("BINANCE_API_KEY") or os.getenv("BINANCE_KEY") or ""
        self.api_secret = os.getenv("BINANCE_API_SECRET") or os.getenv("BINANCE_SECRET") or ""

        self.fetch_calls = 0

        if self.api_key:
            self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    def ping(self) -> bool:
        try:
            r = self.session.get(f"{self.cfg.base_url}/fapi/v1/ping", timeout=self.cfg.timeout_s)
            return r.status_code == 200
        except Exception:
            return False

    def get_last_closed_time(self, symbol: str, tf: str) -> Optional[int]:
        data = self._get("/fapi/v1/klines", {"symbol": symbol, "interval": tf, "limit": 2})
        if not data:
            return None
        # беремо передостанній (останній може бути незакритий)
        if len(data) == 1:
            return int(data[0][6])
        return int(data[-2][6])

    def fetch_klines(self, symbol: str, tf: str, start_ms: int, end_ms: int) -> List[Bar]:
        self.fetch_calls += 1

        out: List[Bar] = []

        cursor = int(start_ms) + 1
        end_ms = int(end_ms)

        while True:
            params = {
                "symbol": symbol,
                "interval": tf,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
            data = self._get("/fapi/v1/klines", params)
            if not data:
                break

            bars = [self._kline_to_bar(symbol, tf, k) for k in data]
            out.extend(bars)

            last_close = bars[-1].close_time
            if last_close >= end_ms:
                break

            cursor = last_close + 1

            if len(data) < 1000:
                break

        # фільтр на всякий випадок
        out = [b for b in out if b.close_time <= end_ms and b.close_time > start_ms]
        return out

    def _get(self, path: str, params: dict) -> list | None:
        url = f"{self.cfg.base_url}{path}"
        try:
            r = self.session.get(url, params=params, timeout=self.cfg.timeout_s)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    def _kline_to_bar(self, symbol: str, tf: str, k: list) -> Bar:
        return Bar(
            symbol=symbol,
            tf=tf,
            open_time=int(k[0]),
            close_time=int(k[6]),
            o=float(k[1]),
            h=float(k[2]),
            l=float(k[3]),
            c=float(k[4]),
            v=float(k[5]),
            features_ok=False,
            features_ver="v1",
        )