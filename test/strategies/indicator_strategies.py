from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class Rule:
    column: str
    op: str
    value: Any


@dataclass(frozen=True)
class Strategy:
    strategy_id: str
    side: str  # LONG | SHORT
    rules: List[Rule]


def R(column: str, op: str, value: Any) -> Rule:
    return Rule(column=column, op=op, value=value)


def S(strategy_id: str, side: str, rules: List[Rule]) -> Strategy:
    return Strategy(strategy_id=strategy_id, side=side, rules=rules)


LONG_STRATEGIES: List[Strategy] = [
    # ---------------- 2 rules ----------------
    S("S001", "LONG", [R("ema9_gt_ema21", "==", 1), R("rsi7", ">", 55)]),
    S("S002", "LONG", [R("ema9_gt_ema21", "==", 1), R("adx10", ">", 18)]),
    S("S003", "LONG", [R("ema9_gt_ema21", "==", 1), R("macd_hist", ">", 0)]),
    S("S004", "LONG", [R("ema9_gt_ema21", "==", 1), R("relative_volume10", ">", 1.1)]),
    S("S005", "LONG", [R("ema9_gt_ema21", "==", 1), R("cmf20", ">", 0)]),

    S("S006", "LONG", [R("ema10_gt_ema30", "==", 1), R("rsi10", ">", 55)]),
    S("S007", "LONG", [R("ema10_gt_ema30", "==", 1), R("adx14", ">", 20)]),
    S("S008", "LONG", [R("ema10_gt_ema30", "==", 1), R("macd_hist", ">", 0)]),
    S("S009", "LONG", [R("ema10_gt_ema30", "==", 1), R("relative_volume20", ">", 1.2)]),
    S("S010", "LONG", [R("ema10_gt_ema30", "==", 1), R("obv_rising", "==", 1)]),

    S("S011", "LONG", [R("ema20_gt_ema50", "==", 1), R("rsi14", ">", 55)]),
    S("S012", "LONG", [R("ema20_gt_ema50", "==", 1), R("adx14", ">", 20)]),
    S("S013", "LONG", [R("ema20_gt_ema50", "==", 1), R("macd_hist", ">", 0)]),
    S("S014", "LONG", [R("ema20_gt_ema50", "==", 1), R("relative_volume20", ">", 1.2)]),
    S("S015", "LONG", [R("ema20_gt_ema50", "==", 1), R("cmf20", ">", 0)]),

    S("S016", "LONG", [R("ema50_gt_ema100", "==", 1), R("rsi14", ">", 55)]),
    S("S017", "LONG", [R("ema50_gt_ema100", "==", 1), R("adx20", ">", 20)]),
    S("S018", "LONG", [R("ema50_gt_ema100", "==", 1), R("macd_hist", ">", 0)]),
    S("S019", "LONG", [R("ema50_gt_ema100", "==", 1), R("relative_volume50", ">", 1.2)]),
    S("S020", "LONG", [R("ema50_gt_ema100", "==", 1), R("close_above_vwap", "==", 1)]),

    S("S021", "LONG", [R("ema50_gt_ema200", "==", 1), R("rsi21", ">", 55)]),
    S("S022", "LONG", [R("ema50_gt_ema200", "==", 1), R("adx20", ">", 25)]),
    S("S023", "LONG", [R("ema50_gt_ema200", "==", 1), R("macd_hist", ">", 0)]),
    S("S024", "LONG", [R("ema50_gt_ema200", "==", 1), R("relative_volume20", ">", 1.2)]),
    S("S025", "LONG", [R("ema50_gt_ema200", "==", 1), R("supertrend_bullish", "==", 1)]),

    S("S026", "LONG", [R("close_above_vwap", "==", 1), R("rsi7", ">", 55)]),
    S("S027", "LONG", [R("close_above_vwap", "==", 1), R("rsi14", ">", 55)]),
    S("S028", "LONG", [R("close_above_vwap", "==", 1), R("adx14", ">", 20)]),
    S("S029", "LONG", [R("close_above_vwap", "==", 1), R("macd_hist", ">", 0)]),
    S("S030", "LONG", [R("close_above_vwap", "==", 1), R("relative_volume20", ">", 1.2)]),

    S("S031", "LONG", [R("supertrend_bullish", "==", 1), R("rsi10", ">", 55)]),
    S("S032", "LONG", [R("supertrend_bullish", "==", 1), R("adx14", ">", 20)]),
    S("S033", "LONG", [R("supertrend_bullish", "==", 1), R("macd_hist", ">", 0)]),
    S("S034", "LONG", [R("supertrend_bullish", "==", 1), R("relative_volume20", ">", 1.2)]),
    S("S035", "LONG", [R("supertrend_bullish", "==", 1), R("cmf20", ">", 0)]),

    S("S036", "LONG", [R("bb20_2_breakout_long", "==", 1), R("relative_volume10", ">", 1.1)]),
    S("S037", "LONG", [R("bb20_2_breakout_long", "==", 1), R("adx10", ">", 18)]),
    S("S038", "LONG", [R("bb20_25_breakout_long", "==", 1), R("relative_volume20", ">", 1.2)]),
    S("S039", "LONG", [R("bb20_25_breakout_long", "==", 1), R("adx14", ">", 20)]),
    S("S040", "LONG", [R("bb30_2_breakout_long", "==", 1), R("relative_volume20", ">", 1.2)]),

    S("S041", "LONG", [R("donchian10_breakout_long", "==", 1), R("relative_volume10", ">", 1.1)]),
    S("S042", "LONG", [R("donchian10_breakout_long", "==", 1), R("adx10", ">", 18)]),
    S("S043", "LONG", [R("donchian20_breakout_long", "==", 1), R("relative_volume20", ">", 1.2)]),
    S("S044", "LONG", [R("donchian20_breakout_long", "==", 1), R("adx14", ">", 20)]),
    S("S045", "LONG", [R("donchian30_breakout_long", "==", 1), R("relative_volume20", ">", 1.2)]),
    S("S046", "LONG", [R("donchian30_breakout_long", "==", 1), R("adx20", ">", 20)]),
    S("S047", "LONG", [R("donchian50_breakout_long", "==", 1), R("relative_volume50", ">", 1.2)]),
    S("S048", "LONG", [R("donchian50_breakout_long", "==", 1), R("adx20", ">", 25)]),

    S("S049", "LONG", [R("rsi7", ">", 60), R("adx10", ">", 18)]),
    S("S050", "LONG", [R("rsi10", ">", 58), R("adx14", ">", 20)]),
    S("S051", "LONG", [R("rsi14", ">", 55), R("adx14", ">", 20)]),
    S("S052", "LONG", [R("rsi21", ">", 55), R("adx20", ">", 25)]),
    S("S053", "LONG", [R("rsi14", ">", 55), R("macd_hist", ">", 0)]),
    S("S054", "LONG", [R("rsi14", ">", 55), R("relative_volume20", ">", 1.2)]),
    S("S055", "LONG", [R("rsi14", ">", 55), R("cmf20", ">", 0)]),

    S("S056", "LONG", [R("cci20", ">", 100), R("adx14", ">", 20)]),
    S("S057", "LONG", [R("cci20", ">", 100), R("relative_volume20", ">", 1.2)]),
    S("S058", "LONG", [R("stoch_rsi14", ">", 0.8), R("adx14", ">", 20)]),
    S("S059", "LONG", [R("stoch_rsi14", ">", 0.8), R("relative_volume20", ">", 1.2)]),
    S("S060", "LONG", [R("obv_rising", "==", 1), R("cmf20", ">", 0)]),

    # ---------------- 3 rules ----------------
    S("S061", "LONG", [R("ema9_gt_ema21", "==", 1), R("rsi7", ">", 55), R("adx10", ">", 18)]),
    S("S062", "LONG", [R("ema9_gt_ema21", "==", 1), R("macd_hist", ">", 0), R("relative_volume10", ">", 1.1)]),
    S("S063", "LONG", [R("ema9_gt_ema21", "==", 1), R("rsi7", ">", 55), R("cmf20", ">", 0)]),
    S("S064", "LONG", [R("ema9_gt_ema21", "==", 1), R("adx10", ">", 18), R("relative_volume10", ">", 1.1)]),

    S("S065", "LONG", [R("ema10_gt_ema30", "==", 1), R("rsi10", ">", 55), R("adx14", ">", 20)]),
    S("S066", "LONG", [R("ema10_gt_ema30", "==", 1), R("macd_hist", ">", 0), R("relative_volume20", ">", 1.2)]),
    S("S067", "LONG", [R("ema10_gt_ema30", "==", 1), R("rsi10", ">", 55), R("cmf20", ">", 0)]),
    S("S068", "LONG", [R("ema10_gt_ema30", "==", 1), R("adx14", ">", 20), R("obv_rising", "==", 1)]),

    S("S069", "LONG", [R("ema20_gt_ema50", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20)]),
    S("S070", "LONG", [R("ema20_gt_ema50", "==", 1), R("macd_hist", ">", 0), R("relative_volume20", ">", 1.2)]),
    S("S071", "LONG", [R("ema20_gt_ema50", "==", 1), R("rsi14", ">", 55), R("macd_hist", ">", 0)]),
    S("S072", "LONG", [R("ema20_gt_ema50", "==", 1), R("rsi14", ">", 55), R("cmf20", ">", 0)]),
    S("S073", "LONG", [R("ema20_gt_ema50", "==", 1), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),

    S("S074", "LONG", [R("ema50_gt_ema100", "==", 1), R("rsi14", ">", 55), R("adx20", ">", 20)]),
    S("S075", "LONG", [R("ema50_gt_ema100", "==", 1), R("macd_hist", ">", 0), R("relative_volume50", ">", 1.2)]),
    S("S076", "LONG", [R("ema50_gt_ema100", "==", 1), R("close_above_vwap", "==", 1), R("adx20", ">", 20)]),
    S("S077", "LONG", [R("ema50_gt_ema100", "==", 1), R("rsi14", ">", 55), R("cmf20", ">", 0)]),

    S("S078", "LONG", [R("ema50_gt_ema200", "==", 1), R("rsi21", ">", 55), R("adx20", ">", 25)]),
    S("S079", "LONG", [R("ema50_gt_ema200", "==", 1), R("macd_hist", ">", 0), R("relative_volume20", ">", 1.2)]),
    S("S080", "LONG", [R("ema50_gt_ema200", "==", 1), R("supertrend_bullish", "==", 1), R("adx20", ">", 20)]),
    S("S081", "LONG", [R("ema50_gt_ema200", "==", 1), R("rsi21", ">", 55), R("close_above_vwap", "==", 1)]),

    S("S082", "LONG", [R("close_above_vwap", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20)]),
    S("S083", "LONG", [R("close_above_vwap", "==", 1), R("macd_hist", ">", 0), R("relative_volume20", ">", 1.2)]),
    S("S084", "LONG", [R("close_above_vwap", "==", 1), R("rsi14", ">", 55), R("cmf20", ">", 0)]),
    S("S085", "LONG", [R("close_above_vwap", "==", 1), R("adx14", ">", 20), R("obv_rising", "==", 1)]),

    S("S086", "LONG", [R("supertrend_bullish", "==", 1), R("rsi10", ">", 55), R("adx14", ">", 20)]),
    S("S087", "LONG", [R("supertrend_bullish", "==", 1), R("macd_hist", ">", 0), R("relative_volume20", ">", 1.2)]),
    S("S088", "LONG", [R("supertrend_bullish", "==", 1), R("rsi10", ">", 55), R("cmf20", ">", 0)]),
    S("S089", "LONG", [R("supertrend_bullish", "==", 1), R("adx14", ">", 20), R("obv_rising", "==", 1)]),

    S("S090", "LONG", [R("bb20_2_breakout_long", "==", 1), R("relative_volume10", ">", 1.1), R("adx10", ">", 18)]),
    S("S091", "LONG", [R("bb20_25_breakout_long", "==", 1), R("relative_volume20", ">", 1.2), R("adx14", ">", 20)]),
    S("S092", "LONG", [R("bb30_2_breakout_long", "==", 1), R("relative_volume20", ">", 1.2), R("rsi14", ">", 55)]),

    S("S093", "LONG", [R("donchian10_breakout_long", "==", 1), R("relative_volume10", ">", 1.1), R("adx10", ">", 18)]),
    S("S094", "LONG", [R("donchian20_breakout_long", "==", 1), R("relative_volume20", ">", 1.2), R("adx14", ">", 20)]),
    S("S095", "LONG", [R("donchian30_breakout_long", "==", 1), R("relative_volume20", ">", 1.2), R("rsi14", ">", 55)]),
    S("S096", "LONG", [R("donchian50_breakout_long", "==", 1), R("relative_volume50", ">", 1.2), R("adx20", ">", 25)]),

    S("S097", "LONG", [R("rsi7", ">", 60), R("adx10", ">", 18), R("relative_volume10", ">", 1.1)]),
    S("S098", "LONG", [R("rsi10", ">", 58), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S099", "LONG", [R("rsi14", ">", 55), R("macd_hist", ">", 0), R("cmf20", ">", 0)]),
    S("S100", "LONG", [R("cci20", ">", 100), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),

    # ---------------- 4 rules ----------------
    S("S101", "LONG", [R("ema9_gt_ema21", "==", 1), R("rsi7", ">", 55), R("adx10", ">", 18), R("relative_volume10", ">", 1.1)]),
    S("S102", "LONG", [R("ema10_gt_ema30", "==", 1), R("rsi10", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S103", "LONG", [R("ema20_gt_ema50", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S104", "LONG", [R("ema20_gt_ema50", "==", 1), R("rsi14", ">", 55), R("macd_hist", ">", 0), R("relative_volume20", ">", 1.2)]),
    S("S105", "LONG", [R("ema20_gt_ema50", "==", 1), R("rsi14", ">", 55), R("cmf20", ">", 0), R("obv_rising", "==", 1)]),

    S("S106", "LONG", [R("ema50_gt_ema100", "==", 1), R("rsi14", ">", 55), R("adx20", ">", 20), R("close_above_vwap", "==", 1)]),
    S("S107", "LONG", [R("ema50_gt_ema200", "==", 1), R("rsi21", ">", 55), R("adx20", ">", 25), R("relative_volume20", ">", 1.2)]),
    S("S108", "LONG", [R("ema50_gt_ema200", "==", 1), R("supertrend_bullish", "==", 1), R("rsi21", ">", 55), R("adx20", ">", 20)]),

    S("S109", "LONG", [R("close_above_vwap", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S110", "LONG", [R("close_above_vwap", "==", 1), R("macd_hist", ">", 0), R("cmf20", ">", 0), R("obv_rising", "==", 1)]),

    S("S111", "LONG", [R("supertrend_bullish", "==", 1), R("rsi10", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S112", "LONG", [R("supertrend_bullish", "==", 1), R("macd_hist", ">", 0), R("cmf20", ">", 0), R("obv_rising", "==", 1)]),

    S("S113", "LONG", [R("bb20_2_breakout_long", "==", 1), R("relative_volume10", ">", 1.1), R("adx10", ">", 18), R("rsi7", ">", 55)]),
    S("S114", "LONG", [R("bb20_25_breakout_long", "==", 1), R("relative_volume20", ">", 1.2), R("adx14", ">", 20), R("rsi14", ">", 55)]),
    S("S115", "LONG", [R("bb30_2_breakout_long", "==", 1), R("relative_volume20", ">", 1.2), R("adx14", ">", 20), R("macd_hist", ">", 0)]),

    S("S116", "LONG", [R("donchian10_breakout_long", "==", 1), R("relative_volume10", ">", 1.1), R("adx10", ">", 18), R("rsi7", ">", 55)]),
    S("S117", "LONG", [R("donchian20_breakout_long", "==", 1), R("relative_volume20", ">", 1.2), R("adx14", ">", 20), R("rsi14", ">", 55)]),
    S("S118", "LONG", [R("donchian30_breakout_long", "==", 1), R("relative_volume20", ">", 1.2), R("adx14", ">", 20), R("macd_hist", ">", 0)]),
    S("S119", "LONG", [R("donchian50_breakout_long", "==", 1), R("relative_volume50", ">", 1.2), R("adx20", ">", 25), R("rsi21", ">", 55)]),

    S("S120", "LONG", [R("rsi14", ">", 55), R("adx14", ">", 20), R("cmf20", ">", 0), R("obv_rising", "==", 1)]),

    # ---------------- 5 rules ----------------
    S("S121", "LONG", [R("ema20_gt_ema50", "==", 1), R("close_above_vwap", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S122", "LONG", [R("ema20_gt_ema50", "==", 1), R("supertrend_bullish", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S123", "LONG", [R("ema50_gt_ema200", "==", 1), R("close_above_vwap", "==", 1), R("rsi21", ">", 55), R("adx20", ">", 25), R("relative_volume20", ">", 1.2)]),
    S("S124", "LONG", [R("ema50_gt_ema200", "==", 1), R("supertrend_bullish", "==", 1), R("rsi21", ">", 55), R("adx20", ">", 25), R("relative_volume20", ">", 1.2)]),

    S("S125", "LONG", [R("bb20_25_breakout_long", "==", 1), R("ema20_gt_ema50", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S126", "LONG", [R("bb30_2_breakout_long", "==", 1), R("close_above_vwap", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),

    S("S127", "LONG", [R("donchian20_breakout_long", "==", 1), R("ema20_gt_ema50", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S128", "LONG", [R("donchian30_breakout_long", "==", 1), R("close_above_vwap", "==", 1), R("rsi14", ">", 55), R("adx14", ">", 20), R("relative_volume20", ">", 1.2)]),
    S("S129", "LONG", [R("donchian50_breakout_long", "==", 1), R("ema50_gt_ema200", "==", 1), R("rsi21", ">", 55), R("adx20", ">", 25), R("relative_volume50", ">", 1.2)]),

    S("S130", "LONG", [R("ema10_gt_ema30", "==", 1), R("supertrend_bullish", "==", 1), R("rsi10", ">", 55), R("adx14", ">", 20), R("cmf20", ">", 0)]),
    S("S131", "LONG", [R("ema9_gt_ema21", "==", 1), R("close_above_vwap", "==", 1), R("rsi7", ">", 55), R("adx10", ">", 18), R("relative_volume10", ">", 1.1)]),

    S("S132", "LONG", [R("ema20_gt_ema50", "==", 1), R("macd_hist", ">", 0), R("rsi14", ">", 55), R("cmf20", ">", 0), R("obv_rising", "==", 1)]),
    S("S133", "LONG", [R("ema50_gt_ema100", "==", 1), R("macd_hist", ">", 0), R("rsi14", ">", 55), R("adx20", ">", 20), R("relative_volume50", ">", 1.2)]),

    S("S134", "LONG", [R("rsi14", ">", 55), R("adx14", ">", 20), R("macd_hist", ">", 0), R("cmf20", ">", 0), R("relative_volume20", ">", 1.2)]),
    S("S135", "LONG", [R("rsi21", ">", 55), R("adx20", ">", 25), R("macd_hist", ">", 0), R("close_above_vwap", "==", 1), R("relative_volume20", ">", 1.2)]),

    S("S136", "LONG", [R("cci20", ">", 100), R("adx14", ">", 20), R("relative_volume20", ">", 1.2), R("cmf20", ">", 0), R("obv_rising", "==", 1)]),
    S("S137", "LONG", [R("stoch_rsi14", ">", 0.8), R("adx14", ">", 20), R("relative_volume20", ">", 1.2), R("macd_hist", ">", 0), R("ema20_gt_ema50", "==", 1)]),

    S("S138", "LONG", [R("bb20_2_breakout_long", "==", 1), R("donchian10_breakout_long", "==", 1), R("adx10", ">", 18), R("relative_volume10", ">", 1.1), R("rsi7", ">", 55)]),
    S("S139", "LONG", [R("bb20_25_breakout_long", "==", 1), R("donchian20_breakout_long", "==", 1), R("adx14", ">", 20), R("relative_volume20", ">", 1.2), R("rsi14", ">", 55)]),
    S("S140", "LONG", [R("bb30_2_breakout_long", "==", 1), R("donchian30_breakout_long", "==", 1), R("adx14", ">", 20), R("relative_volume20", ">", 1.2), R("macd_hist", ">", 0)]),
]


def mirror_rule_for_short(rule: Rule) -> Rule:
    c = rule.column
    op = rule.op
    v = rule.value

    if c in {
        "ema9_gt_ema21",
        "ema10_gt_ema30",
        "ema20_gt_ema50",
        "ema50_gt_ema100",
        "ema50_gt_ema200",
        "close_above_vwap",
        "obv_rising",
    }:
        return Rule(c, "==", 0)

    if c == "supertrend_bullish":
        return Rule("supertrend_bearish", "==", 1)

    if c == "bb20_2_breakout_long":
        return Rule("bb20_2_breakout_short", "==", 1)
    if c == "bb20_25_breakout_long":
        return Rule("bb20_25_breakout_short", "==", 1)
    if c == "bb30_2_breakout_long":
        return Rule("bb30_2_breakout_short", "==", 1)

    if c == "donchian10_breakout_long":
        return Rule("donchian10_breakout_short", "==", 1)
    if c == "donchian20_breakout_long":
        return Rule("donchian20_breakout_short", "==", 1)
    if c == "donchian30_breakout_long":
        return Rule("donchian30_breakout_short", "==", 1)
    if c == "donchian50_breakout_long":
        return Rule("donchian50_breakout_short", "==", 1)

    if c.startswith("rsi") and op == ">":
        return Rule(c, "<", 100 - float(v))

    if c == "macd_hist" and op == ">":
        return Rule("macd_hist", "<", 0)

    if c == "cmf20" and op == ">":
        return Rule("cmf20", "<", 0)

    if c == "cci20" and op == ">" and float(v) == 100:
        return Rule("cci20", "<", -100)

    if c == "stoch_rsi14" and op == ">" and float(v) == 0.8:
        return Rule("stoch_rsi14", "<", 0.2)

    if c.startswith("adx"):
        return rule

    if c.startswith("relative_volume"):
        return rule

    return rule


SHORT_STRATEGIES: List[Strategy] = []
for st in LONG_STRATEGIES:
    sid_num = int(st.strategy_id[1:]) + 140
    short_id = f"S{sid_num:03d}"
    short_rules = [mirror_rule_for_short(r) for r in st.rules]
    SHORT_STRATEGIES.append(S(short_id, "SHORT", short_rules))


ALL_STRATEGIES: List[Strategy] = LONG_STRATEGIES + SHORT_STRATEGIES


def get_all_strategies() -> List[Strategy]:
    return ALL_STRATEGIES


def get_long_strategies() -> List[Strategy]:
    return LONG_STRATEGIES


def get_short_strategies() -> List[Strategy]:
    return SHORT_STRATEGIES


def get_strategy_map() -> Dict[str, Strategy]:
    return {s.strategy_id: s for s in ALL_STRATEGIES}


def get_strategies_by_rule_count(rule_count: int) -> List[Strategy]:
    return [s for s in ALL_STRATEGIES if len(s.rules) == rule_count]