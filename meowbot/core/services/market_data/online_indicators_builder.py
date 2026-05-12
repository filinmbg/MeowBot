from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import replace
from math import isfinite

import numpy as np
import pandas as pd

from meowbot.core.configs.indicator_sets import IndicatorSetConfig
from meowbot.core.domain.types import Bar


log = logging.getLogger("meowbot")


class OnlineIndicatorsBuilder:
    def __init__(self, indicator_set: IndicatorSetConfig):
        self.indicator_set = indicator_set
        self._scaling_self_test_logged = False
        self._suspicious_volume_ratio_logged_keys: set[tuple[str | None, str | None]] = set()
        self.breakdown_log_min_ms = int(os.getenv("INDICATOR_BREAKDOWN_LOG_MIN_MS", "0"))
        self.critical_threshold_ms = int(os.getenv("INDICATOR_BUILD_CRITICAL_MS", "1000"))
        self.last_breakdown: dict[str, int | str | None] = {}
        self._thread_local = threading.local()

    def build_with_breakdown(
        self,
        bars: list[Bar],
        *,
        features_ver: str,
    ) -> tuple[list[Bar], dict[str, int | str | None]]:
        built = self.build(bars, features_ver=features_ver)
        return built, dict(getattr(self._thread_local, "last_breakdown", {}) or self.last_breakdown)

    def build(self, bars: list[Bar], *, features_ver: str) -> list[Bar]:
        if not bars:
            return []

        total_started_at = time.perf_counter()
        profile: dict[str, int] = {
            "copy_ms": 0,
            "concat_ms": 0,
            "cleanup_ms": 0,
        }
        op_counts: dict[str, int] = {
            "df_copy": 0,
            "concat": 0,
            "append": 0,
            "merge": 0,
            "reset_index": 0,
        }

        def timed_ms(name: str, func):
            started_at = time.perf_counter()
            try:
                return func()
            finally:
                profile[name] = profile.get(name, 0) + int((time.perf_counter() - started_at) * 1000)

        copy_started_at = time.perf_counter()
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
        profile["copy_ms"] += int((time.perf_counter() - copy_started_at) * 1000)

        timed_ms("ema_ms", lambda: self._apply_ema(df))
        timed_ms("rsi_ms", lambda: self._apply_rsi(df))
        timed_ms("atr_ms", lambda: self._apply_atr(df, profile=profile, op_counts=op_counts))
        timed_ms("macd_ms", lambda: self._apply_macd(df))
        timed_ms("volume_ratio_ms", lambda: self._apply_relative_volume(df))
        timed_ms("adx_ms", lambda: self._apply_adx(df, profile=profile, op_counts=op_counts))
        timed_ms("vol_peak_ms", lambda: self._apply_volume_peak_offset(df))
        timed_ms("supertrend_ms", lambda: self._apply_supertrend(df, profile=profile, op_counts=op_counts))
        timed_ms("cleanup_ms", lambda: self._apply_runtime_feature_aliases(df))

        required_features = self.indicator_set.required_feature_names()
        cleanup_started_at = time.perf_counter()
        self._log_runtime_feature_integrity(
            df=df,
            bars_sorted=bars_sorted,
            features_ver=features_ver,
            required_features=required_features,
        )
        result: list[Bar] = []
        feature_columns = [name for name in required_features if name in df.columns]
        feature_arrays = {
            name: df[name].to_numpy(dtype=float, copy=False)
            for name in feature_columns
        }

        for index, original_bar in enumerate(bars_sorted):
            features: dict[str, float] = {}

            for name, values in feature_arrays.items():
                value = values[index]
                if np.isnan(value):
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

        profile["cleanup_ms"] += int((time.perf_counter() - cleanup_started_at) * 1000)
        total_ms = int((time.perf_counter() - total_started_at) * 1000)
        last_bar = bars_sorted[-1]
        breakdown: dict[str, int | str | None] = {
            "symbol": getattr(last_bar, "symbol", None),
            "tf": getattr(last_bar, "tf", None),
            "features_ver": features_ver,
            "rows": len(bars_sorted),
            "total_ms": total_ms,
            "rsi_ms": profile.get("rsi_ms", 0),
            "ema_ms": profile.get("ema_ms", 0),
            "atr_ms": profile.get("atr_ms", 0),
            "adx_ms": profile.get("adx_ms", 0),
            "supertrend_ms": profile.get("supertrend_ms", 0),
            "volume_ratio_ms": profile.get("volume_ratio_ms", 0),
            "vol_peak_ms": profile.get("vol_peak_ms", 0),
            "copy_ms": profile.get("copy_ms", 0),
            "concat_ms": profile.get("concat_ms", 0),
            "cleanup_ms": profile.get("cleanup_ms", 0),
            "df_copy_count": op_counts.get("df_copy", 0),
            "concat_count": op_counts.get("concat", 0),
            "append_count": op_counts.get("append", 0),
            "merge_count": op_counts.get("merge", 0),
            "reset_index_count": op_counts.get("reset_index", 0),
        }
        self.last_breakdown = breakdown
        self._thread_local.last_breakdown = breakdown
        if total_ms >= self.breakdown_log_min_ms:
            log.info(
                "[indicator-breakdown] symbol=%s tf=%s rows=%s total_ms=%s rsi_ms=%s ema_ms=%s atr_ms=%s adx_ms=%s supertrend_ms=%s volume_ratio_ms=%s vol_peak_ms=%s copy_ms=%s concat_ms=%s cleanup_ms=%s df_copy_count=%s concat_count=%s append_count=%s merge_count=%s reset_index_count=%s",
                breakdown["symbol"],
                breakdown["tf"],
                breakdown["rows"],
                breakdown["total_ms"],
                breakdown["rsi_ms"],
                breakdown["ema_ms"],
                breakdown["atr_ms"],
                breakdown["adx_ms"],
                breakdown["supertrend_ms"],
                breakdown["volume_ratio_ms"],
                breakdown["vol_peak_ms"],
                breakdown["copy_ms"],
                breakdown["concat_ms"],
                breakdown["cleanup_ms"],
                breakdown["df_copy_count"],
                breakdown["concat_count"],
                breakdown["append_count"],
                breakdown["merge_count"],
                breakdown["reset_index_count"],
            )
        if total_ms > self.critical_threshold_ms:
            log.critical(
                "[indicator-breakdown] CRITICAL_SLOW_INDICATOR_BUILD symbol=%s tf=%s rows=%s total_ms=%s threshold_ms=%s breakdown=%s",
                breakdown["symbol"],
                breakdown["tf"],
                breakdown["rows"],
                total_ms,
                self.critical_threshold_ms,
                breakdown,
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

    def _true_range(
        self,
        df: pd.DataFrame,
        *,
        profile: dict[str, int] | None = None,
        op_counts: dict[str, int] | None = None,
    ) -> pd.Series:
        prev_close = df["close"].shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - prev_close).abs()
        tr3 = (df["low"] - prev_close).abs()
        started_at = time.perf_counter()
        result = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        if profile is not None:
            profile["concat_ms"] = profile.get("concat_ms", 0) + int((time.perf_counter() - started_at) * 1000)
        if op_counts is not None:
            op_counts["concat"] = op_counts.get("concat", 0) + 1
        return result

    def _apply_atr(
        self,
        df: pd.DataFrame,
        *,
        profile: dict[str, int] | None = None,
        op_counts: dict[str, int] | None = None,
    ) -> None:
        tr = self._true_range(df, profile=profile, op_counts=op_counts)

        for period in self.indicator_set.atr.periods:
            atr_series = tr.ewm(alpha=1 / period, adjust=False).mean()
            df[f"atr{period}"] = atr_series

        for period in self.indicator_set.atr.include_pct_for_periods:
            # Runtime *_pct features are decimal ratios: 1% is stored as 0.01.
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
            volume_sma = df["volume"].rolling(period).mean()
            df[f"volume_sma{period}"] = volume_sma
            df[f"relative_volume{period}"] = df["volume"] / volume_sma

    def _apply_adx(
        self,
        df: pd.DataFrame,
        *,
        profile: dict[str, int] | None = None,
        op_counts: dict[str, int] | None = None,
    ) -> None:
        high = df["high"]
        low = df["low"]
        up_move = high.diff()
        down_move = -low.diff()
        tr = self._true_range(df, profile=profile, op_counts=op_counts)

        for period in self.indicator_set.adx.periods:
            plus_dm = pd.Series(
                np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
                index=df.index,
            )
            minus_dm = pd.Series(
                np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
                index=df.index,
            )
            atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
            plus_di = (
                100.0
                * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
                / atr
            )
            minus_di = (
                100.0
                * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
                / atr
            )
            denominator = (plus_di + minus_di).replace(0, np.nan)
            dx = (
                100.0
                * (plus_di - minus_di).abs()
                / denominator
            ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
            adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
            df[f"adx_{period}"] = adx
            df[f"adx{period}"] = adx

    def _apply_volume_peak_offset(self, df: pd.DataFrame) -> None:
        for period in self.indicator_set.volume_peak.periods:
            df[f"vol_peak_offset_{period}"] = self._volume_peak_offset_series(
                df["volume"],
                period=period,
            )

    @staticmethod
    def _volume_peak_offset_series(volume: pd.Series, *, period: int) -> pd.Series:
        values = volume.to_numpy(dtype=float, copy=False)
        result = np.full(values.shape[0], np.nan, dtype=float)
        if period <= 0 or values.shape[0] < period:
            return pd.Series(result, index=volume.index)

        windows = np.lib.stride_tricks.sliding_window_view(values, period)
        valid = ~np.isnan(windows).all(axis=1)
        if not np.any(valid):
            return pd.Series(result, index=volume.index)

        max_values = np.nanmax(windows[valid], axis=1)
        matches = windows[valid] == max_values[:, None]
        # Prefer the most recent peak when volumes tie.
        reversed_positions = np.argmax(matches[:, ::-1], axis=1)
        last_peak_positions = (period - 1) - reversed_positions
        offsets = last_peak_positions - (period - 1)

        target = result[period - 1 :]
        target[valid] = offsets.astype(float)
        result[period - 1 :] = target
        return pd.Series(result, index=volume.index)

    @staticmethod
    def _latest_peak_offset(values: np.ndarray) -> float:
        if values.size == 0 or np.isnan(values).all():
            return float("nan")
        peak = np.nanmax(values)
        positions = np.flatnonzero(values == peak)
        if positions.size == 0:
            return float("nan")
        # 0 means current bar, -1 previous bar, etc. Prefer the most recent
        # peak when volumes tie so flat volume does not look like an old spike.
        return float(int(positions[-1]) - (len(values) - 1))

    def _apply_runtime_feature_aliases(self, df: pd.DataFrame) -> None:
        if "rsi14" in df.columns:
            df["rsi_14"] = df["rsi14"]
        if "ema50" in df.columns:
            df["ema_50"] = df["ema50"]
            # Keep unit convention aligned with atr*_pct: 1% is stored as 0.01.
            df["dist_to_ema_50_pct"] = (
                (df["close"] - df["ema50"])
                / df["ema50"].replace(0, np.nan)
            )
        if "atr14_pct" in df.columns:
            df["atr_14_pct"] = df["atr14_pct"]
        if "relative_volume20" in df.columns:
            if "volume_sma20" in df.columns:
                df["volume_sma_20"] = df["volume_sma20"]
            df["volume_ratio_sma_20"] = df["relative_volume20"]

        candle_range = (df["high"] - df["low"]).replace(0, np.nan)
        df["close_position_in_candle"] = (df["close"] - df["low"]) / candle_range

    def _apply_supertrend(
        self,
        df: pd.DataFrame,
        *,
        profile: dict[str, int] | None = None,
        op_counts: dict[str, int] | None = None,
    ) -> None:
        for variant in self.indicator_set.supertrend:
            df[variant.bullish_feature_name] = self._supertrend_bullish(
                df=df,
                period=variant.period,
                multiplier=variant.multiplier,
                profile=profile,
                op_counts=op_counts,
            )

    def _supertrend_bullish(
        self,
        *,
        df: pd.DataFrame,
        period: int,
        multiplier: float,
        profile: dict[str, int] | None = None,
        op_counts: dict[str, int] | None = None,
    ) -> pd.Series:
        hl2 = (df["high"] + df["low"]) / 2
        atr_val = self._true_range(df, profile=profile, op_counts=op_counts).ewm(alpha=1 / period, adjust=False).mean()

        upperband = hl2 + multiplier * atr_val
        lowerband = hl2 - multiplier * atr_val

        copy_started_at = time.perf_counter()
        close = df["close"].to_numpy(dtype=float, copy=False)
        upper = upperband.to_numpy(dtype=float, copy=True)
        lower = lowerband.to_numpy(dtype=float, copy=True)
        final_upper = upper.copy()
        final_lower = lower.copy()
        direction = np.ones(len(df), dtype=np.int8)
        if profile is not None:
            profile["copy_ms"] = profile.get("copy_ms", 0) + int((time.perf_counter() - copy_started_at) * 1000)
        if op_counts is not None:
            op_counts["df_copy"] = op_counts.get("df_copy", 0) + 4

        for i in range(1, len(close)):
            if (
                upper[i] < final_upper[i - 1]
                or close[i - 1] > final_upper[i - 1]
            ):
                final_upper[i] = upper[i]
            else:
                final_upper[i] = final_upper[i - 1]

            if (
                lower[i] > final_lower[i - 1]
                or close[i - 1] < final_lower[i - 1]
            ):
                final_lower[i] = lower[i]
            else:
                final_lower[i] = final_lower[i - 1]

            if direction[i - 1] == -1 and close[i] > final_upper[i]:
                direction[i] = 1
            elif direction[i - 1] == 1 and close[i] < final_lower[i]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]

        return pd.Series((direction == 1).astype(int), index=df.index)

    def _log_runtime_feature_integrity(
        self,
        *,
        df: pd.DataFrame,
        bars_sorted: list[Bar],
        features_ver: str,
        required_features: tuple[str, ...],
    ) -> None:
        if df.empty or not bars_sorted:
            return

        last_bar = bars_sorted[-1]
        symbol = getattr(last_bar, "symbol", None)
        tf = getattr(last_bar, "tf", None)
        last = df.iloc[-1]

        self._log_scaling_self_test_once(
            symbol=symbol,
            tf=tf,
            close_time=getattr(last_bar, "close_time", None),
            features_ver=features_ver,
            last=last,
        )

        log.debug(
            "[features-debug] symbol=%s tf=%s close_time=%s features_ver=%s atr_raw=%s close=%s atr_14_pct=%s dist_to_ema_50_pct=%s volume=%s volume_sma_20=%s volume_ratio_sma_20=%s close_position_in_candle=%s adx_14=%s vol_peak_offset_10=%s columns=%s",
            symbol,
            tf,
            getattr(last_bar, "close_time", None),
            features_ver,
            self._safe_row_value(last, "atr14"),
            self._safe_row_value(last, "close"),
            self._safe_row_value(last, "atr_14_pct"),
            self._safe_row_value(last, "dist_to_ema_50_pct"),
            self._safe_row_value(last, "volume"),
            self._safe_row_value(last, "volume_sma_20"),
            self._safe_row_value(last, "volume_ratio_sma_20"),
            self._safe_row_value(last, "close_position_in_candle"),
            self._safe_row_value(last, "adx_14"),
            self._safe_row_value(last, "vol_peak_offset_10"),
            list(df.columns),
        )
        self._log_suspicious_volume_ratio(df=df, symbol=symbol, tf=tf, features_ver=features_ver)

        missing_columns = [name for name in required_features if name not in df.columns]
        if missing_columns:
            log.warning(
                "[features] FEATURE_PIPELINE_MISSING symbol=%s tf=%s features_ver=%s missing_columns=%s columns=%s",
                symbol,
                tf,
                features_ver,
                missing_columns,
                list(df.columns),
            )

        for feature_name in ("adx_14", "vol_peak_offset_10"):
            if feature_name not in df.columns:
                continue
            value = last[feature_name]
            if pd.isna(value):
                snapshot_columns = [
                    name
                    for name in (
                        "close_time",
                        "close",
                        "high",
                        "low",
                        "volume",
                        "volume_sma_20",
                        "volume_ratio_sma_20",
                        "adx_14",
                        "vol_peak_offset_10",
                    )
                    if name in df.columns
                ]
                log.warning(
                    "[features] FEATURE_NAN_DETECTED symbol=%s tf=%s features_ver=%s feature=%s tail_snapshot=%s",
                    symbol,
                    tf,
                    features_ver,
                    feature_name,
                    df[snapshot_columns].tail(3).to_dict("records"),
                )

    def _log_scaling_self_test_once(
        self,
        *,
        symbol: str | None,
        tf: str | None,
        close_time: int | None,
        features_ver: str,
        last: pd.Series,
    ) -> None:
        if self._scaling_self_test_logged:
            return
        self._scaling_self_test_logged = True
        log.info(
            "[features] ATR scaling mode=decimal_ratio pct_fields=decimal_ratio volume_ratio=ratio close_position=ratio symbol=%s tf=%s close_time=%s features_ver=%s atr_raw=%s close=%s atr_14_pct=%s dist_to_ema_50_pct=%s volume=%s volume_sma_20=%s volume_ratio_sma_20=%s close_position_in_candle=%s",
            symbol,
            tf,
            close_time,
            features_ver,
            self._safe_row_value(last, "atr14"),
            self._safe_row_value(last, "close"),
            self._safe_row_value(last, "atr_14_pct"),
            self._safe_row_value(last, "dist_to_ema_50_pct"),
            self._safe_row_value(last, "volume"),
            self._safe_row_value(last, "volume_sma_20"),
            self._safe_row_value(last, "volume_ratio_sma_20"),
            self._safe_row_value(last, "close_position_in_candle"),
        )

    def _log_suspicious_volume_ratio(
        self,
        *,
        df: pd.DataFrame,
        symbol: str | None,
        tf: str | None,
        features_ver: str,
    ) -> None:
        if "volume_ratio_sma_20" not in df.columns:
            return
        tail = df[["close_time", "volume", "volume_sma_20", "volume_ratio_sma_20"]].tail(5)
        ratios = [
            float(value)
            for value in tail["volume_ratio_sma_20"].tolist()
            if value is not None and not pd.isna(value) and isfinite(float(value))
        ]
        if len(ratios) < 5 or not all(value < 0.05 for value in ratios):
            return
        key = (symbol, tf)
        if key in self._suspicious_volume_ratio_logged_keys:
            return
        self._suspicious_volume_ratio_logged_keys.add(key)
        log.warning(
            "[features] VOLUME_RATIO_SUSPICIOUS symbol=%s tf=%s features_ver=%s last5=%s",
            symbol,
            tf,
            features_ver,
            tail.to_dict("records"),
        )

    @staticmethod
    def _safe_row_value(row: pd.Series, name: str) -> float | None:
        if name not in row:
            return None
        value = row[name]
        if pd.isna(value):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
