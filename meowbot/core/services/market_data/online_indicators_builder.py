from __future__ import annotations

from dataclasses import replace
from math import isfinite

import numpy as np
import pandas as pd

from meowbot.core.configs.indicator_sets import IndicatorSetConfig
from meowbot.core.domain.types import Bar


class OnlineIndicatorsBuilder:
    def __init__(self, indicator_set: IndicatorSetConfig):
        self.indicator_set = indicator_set

    def build(self, bars: list[Bar], *, features_ver: str) -> list[Bar]:
        if not bars:
            return []

        bars_sorted = sorted(bars, key=lambda item: item.close_time)
        df = pd.DataFrame(
            {
                "symbol": [b.symbol for b in bars_sorted],
                "tf": [b.tf for b in bars_sorted],
                "open_time": [b.open_time for b in bars_sorted],
                "close_time": [b.close_time for b in bars_sorted],
                "open": [float(b.o) for b in bars_sorted],
                "high": [float(b.h) for b in bars_sorted],
                "low": [float(b.l) for b in bars_sorted],
                "close": [float(b.c) for b in bars_sorted],
                "volume": [float(b.v) for b in bars_sorted],
            }
        )

        self._apply_ema(df)
        self._apply_rsi(df)
        self._apply_atr(df)
        self._apply_macd(df)
        self._apply_relative_volume(df)
        self._apply_supertrend(df)

        required_features = self.indicator_set.required_feature_names()
        result: list[Bar] = []

        for index, original_bar in enumerate(bars_sorted):
            features: dict[str, float] = {}

            for name in required_features:
                value = df.iloc[index][name]
                if pd.isna(value):
                    continue
                value_float = float(value)
                if not isfinite(value_float):
                    continue
                features[name] = value_float

            features_ok = all(name in features for name in required_features)

            result.append(
                replace(
                    original_bar,
                    features=features,
                    features_ok=features_ok,
                    features_ver=features_ver,
                )
            )

        return result

    def _apply_ema(self, df: pd.DataFrame) -> None:
        for period in self.indicator_set.ema.periods:
            df[f"ema{period}"] = df["close"].ewm(span=period, adjust=False).mean()

    def _apply_rsi(self, df: pd.DataFrame) -> None:
        close = df["close"]
        delta = close.diff()
        up = delta.clip(lower=0.0)
        down = -delta.clip(upper=0.0)

        for period in self.indicator_set.rsi.periods:
            ma_up = up.ewm(alpha=1 / period, adjust=False).mean()
            ma_down = down.ewm(alpha=1 / period, adjust=False).mean()

            rs = ma_up / ma_down.replace(0, np.nan)
            out = 100 - (100 / (1 + rs))
            df[f"rsi{period}"] = out.fillna(50.0)

    def _true_range(self, df: pd.DataFrame) -> pd.Series:
        prev_close = df["close"].shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - prev_close).abs()
        tr3 = (df["low"] - prev_close).abs()
        return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    def _apply_atr(self, df: pd.DataFrame) -> None:
        tr = self._true_range(df)

        for period in self.indicator_set.atr.periods:
            atr_series = tr.ewm(alpha=1 / period, adjust=False).mean()
            df[f"atr{period}"] = atr_series

        for period in self.indicator_set.atr.include_pct_for_periods:
            df[f"atr{period}_pct"] = df[f"atr{period}"] / df["close"]

    def _apply_macd(self, df: pd.DataFrame) -> None:
        cfg = self.indicator_set.macd
        macd_line = (
            df["close"].ewm(span=cfg.fast, adjust=False).mean()
            - df["close"].ewm(span=cfg.slow, adjust=False).mean()
        )
        signal_line = macd_line.ewm(span=cfg.signal, adjust=False).mean()
        hist = macd_line - signal_line

        df[f"{cfg.prefix}_line"] = macd_line
        df[f"{cfg.prefix}_signal"] = signal_line
        df[f"{cfg.prefix}_hist"] = hist

    def _apply_relative_volume(self, df: pd.DataFrame) -> None:
        for period in self.indicator_set.relative_volume.periods:
            df[f"relative_volume{period}"] = (
                df["volume"] / df["volume"].rolling(period).mean()
            )

    def _apply_supertrend(self, df: pd.DataFrame) -> None:
        for variant in self.indicator_set.supertrend:
            df[variant.bullish_feature_name] = self._supertrend_bullish(
                df=df,
                period=variant.period,
                multiplier=variant.multiplier,
            )

    def _supertrend_bullish(
        self,
        *,
        df: pd.DataFrame,
        period: int,
        multiplier: float,
    ) -> pd.Series:
        hl2 = (df["high"] + df["low"]) / 2
        atr_val = self._true_range(df).ewm(alpha=1 / period, adjust=False).mean()

        upperband = hl2 + multiplier * atr_val
        lowerband = hl2 - multiplier * atr_val

        direction = pd.Series(index=df.index, dtype="int64")
        direction.iloc[0] = 1

        final_upper = upperband.copy()
        final_lower = lowerband.copy()

        for i in range(1, len(df)):
            if (
                upperband.iloc[i] < final_upper.iloc[i - 1]
                or df["close"].iloc[i - 1] > final_upper.iloc[i - 1]
            ):
                final_upper.iloc[i] = upperband.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i - 1]

            if (
                lowerband.iloc[i] > final_lower.iloc[i - 1]
                or df["close"].iloc[i - 1] < final_lower.iloc[i - 1]
            ):
                final_lower.iloc[i] = lowerband.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i - 1]

            if direction.iloc[i - 1] == -1 and df["close"].iloc[i] > final_upper.iloc[i]:
                direction.iloc[i] = 1
            elif direction.iloc[i - 1] == 1 and df["close"].iloc[i] < final_lower.iloc[i]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1]

        return (direction == 1).astype(int)