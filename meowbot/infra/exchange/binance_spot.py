from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

from meowbot.core.domain.types import Bar


@dataclass(frozen=True)
class BinanceConfig:
    base_url: str = "https://api.binance.com"  # spot
    timeout_s: float = 10.0
    recv_window: int = 5000


class BinanceSpotMarketData:
    """
    Реалізує порт meowbot.core.ports.exchange_market_data.ExchangeMarketData
    Методи, які вже використовуються у use-cases:
      - get_last_closed_time(symbol, tf) -> Optional[int]
      - fetch_klines(symbol, tf, start_ms, end_ms) -> List[Bar]
    """
    def __init__(self, cfg: BinanceConfig | None = None):
        self.cfg = cfg or BinanceConfig()
        self.session = requests.Session()

        # Ключі (на майбутнє). Для klines не потрібні.
        self.api_key = os.getenv("BINANCE_API_KEY") or os.getenv("BINANCE_KEY") or ""
        self.api_secret = os.getenv("BINANCE_API_SECRET") or os.getenv("BINANCE_SECRET") or ""

        # Для тест/діагностики, як у FakeExchange
        self.fetch_calls = 0

        if self.api_key:
            self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    def ping(self) -> bool:
        try:
            r = self.session.get(f"{self.cfg.base_url}/api/v3/ping", timeout=self.cfg.timeout_s)
            return r.status_code == 200
        except Exception:
            return False

    def get_last_closed_time(self, symbol: str, tf: str) -> Optional[int]:
        """
        Повертає close_time (ms) останнього ЗАКРИТОГО бару.
        На Binance останній kline може бути ще незакритий, тому беремо передостанній.
        """
        params = {"symbol": symbol, "interval": tf, "limit": 2}
        r = self._get("/api/v3/klines", params=params)
        if not r:
            return None
        # kline: [openTime, open, high, low, close, volume, closeTime, ...]
        if len(r) == 1:
            return int(r[0][6])
        return int(r[-2][6])

    def fetch_klines(self, symbol: str, tf: str, start_ms: int, end_ms: int) -> List[Bar]:
        """
        Повертає список Bar у проміжку (start_ms, end_ms] за close_time.
        Реалізовано через пагінацію Binance: максимум 1000 klines за запит.
        """
        self.fetch_calls += 1

        out: List[Bar] = []

        # Щоб не підтягувати дубль бару з close_time == start_ms
        # Binance параметр startTime = openTime, тому ставимо start_ms+1
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
            data = self._get("/api/v3/klines", params=params)
            if not data:
                break

            bars = [self._kline_to_bar(symbol, tf, k) for k in data]
            out.extend(bars)

            # рухаємо курсор вперед
            last_close = bars[-1].close_time
            if last_close >= end_ms:
                break

            # наступний запит починаємо після останнього close_time
            cursor = last_close + 1

            # захист від “вічного” циклу
            if len(data) < 1000:
                break

        # інколи повертається бар за межами endTime через округлення — відфільтруємо
        out = [b for b in out if b.close_time <= end_ms and b.close_time > start_ms]
        return out

    # ---------------- internals ----------------

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
        open_time = int(k[0])
        close_time = int(k[6])
        return Bar(
            symbol=symbol,
            tf=tf,
            open_time=open_time,
            close_time=close_time,
            o=float(k[1]),
            h=float(k[2]),
            l=float(k[3]),
            c=float(k[4]),
            v=float(k[5]),
            # індикатори порахуємо окремо, але для pipeline ставимо як “готово” на цьому етапі
            features_ok=False,
            features_ver="v1",
        )