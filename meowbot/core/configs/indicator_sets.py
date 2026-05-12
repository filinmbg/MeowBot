from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


IndicatorSetName = Literal["v2_core"]


class EmaConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    periods: tuple[int, ...] = (20, 50, 100, 200)


class RsiConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    periods: tuple[int, ...] = (7, 14, 21, 30, 50, 100)


class AtrConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    periods: tuple[int, ...] = (14, 21)
    include_pct_for_periods: tuple[int, ...] = (14,)


class RelativeVolumeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    periods: tuple[int, ...] = (20, 50, 100)


class AdxConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    periods: tuple[int, ...] = (14,)


class VolumePeakConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    periods: tuple[int, ...] = (10,)


class MacdConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    fast: int = 12
    slow: int = 26
    signal: int = 9
    prefix: str = "macd"


class SupertrendVariantConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    period: int
    multiplier: float

    @property
    def bullish_feature_name(self) -> str:
        multiplier_text = str(self.multiplier).replace(".", "_")
        return f"supertrend_bullish_{self.period}_{multiplier_text}"


class IndicatorSetConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: IndicatorSetName
    ema: EmaConfig = Field(default_factory=EmaConfig)
    rsi: RsiConfig = Field(default_factory=RsiConfig)
    atr: AtrConfig = Field(default_factory=AtrConfig)
    relative_volume: RelativeVolumeConfig = Field(default_factory=RelativeVolumeConfig)
    adx: AdxConfig = Field(default_factory=AdxConfig)
    volume_peak: VolumePeakConfig = Field(default_factory=VolumePeakConfig)
    macd: MacdConfig = Field(default_factory=MacdConfig)
    supertrend: tuple[SupertrendVariantConfig, ...] = (
        SupertrendVariantConfig(period=10, multiplier=3.0),
        SupertrendVariantConfig(period=20, multiplier=4.0),
    )

    def required_history_bars(self) -> int:
        candidates = [
            *self.ema.periods,
            *self.rsi.periods,
            *self.atr.periods,
            *self.relative_volume.periods,
            *self.adx.periods,
            *self.volume_peak.periods,
            self.macd.slow + self.macd.signal,
            *(variant.period for variant in self.supertrend),
        ]
        return max(max(candidates), 200) + 20

    def required_feature_names(self) -> tuple[str, ...]:
        names: list[str] = []

        for period in self.ema.periods:
            names.append(f"ema{period}")

        for period in self.rsi.periods:
            names.append(f"rsi{period}")

        for period in self.atr.periods:
            names.append(f"atr{period}")

        for period in self.atr.include_pct_for_periods:
            names.append(f"atr{period}_pct")

        names.extend(
            [
                f"{self.macd.prefix}_line",
                f"{self.macd.prefix}_signal",
                f"{self.macd.prefix}_hist",
            ]
        )

        for period in self.relative_volume.periods:
            names.append(f"volume_sma{period}")
            names.append(f"relative_volume{period}")

        for variant in self.supertrend:
            names.append(variant.bullish_feature_name)

        # Runtime aliases required by V2 strategy rules. Keep the legacy names
        # above for backward compatibility, but persist exact V2 field names too.
        names.extend(
            [
                "rsi_14",
                "ema_50",
                "dist_to_ema_50_pct",
                "atr_14_pct",
                "volume_sma_20",
                "volume_ratio_sma_20",
                "close_position_in_candle",
            ]
        )

        for period in self.adx.periods:
            names.append(f"adx_{period}")

        for period in self.volume_peak.periods:
            names.append(f"vol_peak_offset_{period}")

        return tuple(names)


INDICATOR_SET_V2_CORE = IndicatorSetConfig(name="v2_core")


INDICATOR_SETS: dict[str, IndicatorSetConfig] = {
    INDICATOR_SET_V2_CORE.name: INDICATOR_SET_V2_CORE,
}


def get_indicator_set(features_ver: str) -> IndicatorSetConfig:
    try:
        return INDICATOR_SETS[features_ver]
    except KeyError as exc:
        available = ", ".join(sorted(INDICATOR_SETS.keys()))
        raise ValueError(
            f"Unknown features_ver='{features_ver}'. Available: {available}"
        ) from exc
