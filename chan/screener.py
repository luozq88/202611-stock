"""三买信号识别与单票分析。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from .config import ChanConfig
from .macd import bottom_divergence, macd_series, top_divergence
from .structure import (
    Bar,
    Stroke,
    build_strokes,
    find_fractals,
    find_pivots,
    merge_bars,
)


@dataclass(slots=True)
class ScreenResult:
    ts_code: str
    name: str
    industry: str
    close: float
    pivot_zg: float
    pivot_zd: float
    pivot_gg: float
    pivot_dd: float
    pivot_start: str
    pivot_end: str
    pivot_bars: int
    pivot_width_pct: float
    breakout_date: str
    breakout_high: float
    breakout_pct: float
    strength_ratio: float
    top_divergence: bool
    pullback_status: str
    pullback_low: Optional[float]
    pullback_bars: int
    pullback_low_vs_zg_pct: Optional[float]
    bottom_divergence: bool
    days_since_breakout: int
    volume_ratio: float
    chase_pct: float
    stop_zg: float
    stop_pullback: Optional[float]
    score: float
    market_ok: Optional[bool] = None
    sub_ok: Optional[bool] = None
    sub_note: str = ""


def _find_prev(strokes: Sequence[Stroke], index: int, direction: str) -> Optional[Stroke]:
    for i in range(index - 1, -1, -1):
        if strokes[i].direction == direction:
            return strokes[i]
    return None


def _score(
    *,
    strength_ratio: float,
    pullback_status: str,
    pullback_low: Optional[float],
    zg: float,
    bottom_div: bool,
    days_since_breakout: int,
    volume_ratio: float,
    chase_pct: float,
    top_div: bool,
) -> float:
    score = 0.0
    score += min(strength_ratio, 3.0) / 3.0 * 25.0

    if pullback_low:
        gap = max(pullback_low / zg - 1.0, 0.0)
        score += max(0.0, 1.0 - gap / 0.08) * 20.0
    else:
        score += 5.0

    score += {
        "breakout_only": 8.0,
        "pullback_above_zg": 18.0,
        "daily_pullback": 22.0,
        "second_launch": 28.0,
    }.get(pullback_status, 0.0)

    if bottom_div:
        score += 8.0

    score += max(0.0, 10.0 - days_since_breakout / 3.0)

    if volume_ratio >= 1.5:
        score += 4.0

    if chase_pct > 0.15:
        score -= min(15.0, (chase_pct - 0.15) * 50.0)

    if top_div:
        score -= 10.0

    return max(0.0, min(100.0, score))


def analyze_stock(
    ts_code: str,
    name: str,
    industry: str,
    bars: Sequence[Bar],
    cfg: ChanConfig,
    market_ok: Optional[bool] = None,
) -> Optional[ScreenResult]:
    """分析单只股票，返回最强的一个三买候选。

    核心逻辑：
    1. 找到最近的中枢与向上离开段；
    2. 突破幅度、突破力度、顶背驰过滤；
    3. 突破后所有日线低点必须严格在 ZG 之上（回抽不碰中枢上沿）；
    4. 识别日线回抽状态：仅突破 / 回抽在 ZG 上 / 完整日线回抽 / 二次启动；
    5. 可选 MACD 底背驰、均线、成交额过滤。
    """
    if len(bars) < cfg.min_bars:
        return None

    last_bar = bars[-1]
    if last_bar.close < cfg.min_price:
        return None

    if cfg.min_avg_amount > 0:
        recent = bars[-20:]
        avg_amount = float(np.mean([b.amount for b in recent]))
        if avg_amount < cfg.min_avg_amount:
            return None

    merged = merge_bars(bars)
    if len(merged) < 10:
        return None

    fractals = find_fractals(merged)
    strokes = build_strokes(fractals, cfg.min_stroke_gap)
    if len(strokes) < 4:
        return None

    pivots = find_pivots(strokes, cfg.min_pivot_width_pct, cfg.min_pivot_bars)
    if not pivots:
        return None

    closes = pd.Series([b.close for b in bars], dtype="float64")
    dif, _, hist = macd_series(closes)

    if cfg.use_ma_filter:
        if len(bars) < cfg.ma_long:
            return None
        ma_short = float(closes.iloc[-cfg.ma_short :].mean())
        ma_long = float(closes.iloc[-cfg.ma_long :].mean())
        if not (last_bar.close > ma_short > ma_long):
            return None

    last_idx = len(bars) - 1
    candidates: List[ScreenResult] = []

    for pivot in reversed(pivots):
        if pivot.leave_stroke is None:
            continue

        k = pivot.leave_stroke
        leave = strokes[k]
        if leave.direction != "up":
            # 结构变体：中枢第三笔直接向上突破 ZG，当前笔就是回抽。
            # 此时 find_pivots 记录的 leave 是回抽笔（down），真正的离开段是
            # 中枢的最后一笔（up）。
            last_included = strokes[pivot.end_stroke]
            if (
                leave.direction == "down"
                and last_included.direction == "up"
                and last_included.high > pivot.zg * (1.0 + cfg.breakout_min_pct)
                and leave.low > pivot.zg * (1.0 + cfg.pullback_tolerance)
            ):
                k = pivot.end_stroke
                leave = last_included
            else:
                continue

        # 离开段必须有效突破 ZG
        if leave.high <= pivot.zg * (1.0 + cfg.breakout_min_pct):
            continue

        # 突破不能太陈旧
        if last_idx - leave.end_idx > cfg.max_days_since_breakout:
            continue

        # 突破力度：离开段幅度需超过前一下跌段的一半
        prev_down = _find_prev(strokes, k, "down")
        if prev_down is None or prev_down.amplitude <= 0:
            continue
        strength_ratio = leave.amplitude / prev_down.amplitude
        if strength_ratio < cfg.min_strength_ratio:
            continue

        # 顶背驰过滤
        prev_up = _find_prev(strokes, k, "up")
        top_div = top_divergence(prev_up, leave, dif, hist) if prev_up else False
        if cfg.reject_top_divergence and top_div:
            continue

        # 回抽不碰 ZG：突破高点之后的所有日线低点都在 ZG 之上
        post = bars[leave.end_idx + 1 :]
        low_since = min((b.low for b in post), default=None)
        if low_since is not None and low_since <= pivot.zg * (1.0 + cfg.pullback_tolerance):
            continue

        # 日线回抽状态
        pullback: Optional[Stroke] = None
        pullback_low: Optional[float] = None
        pullback_bars = 0

        if k + 1 < len(strokes) and strokes[k + 1].direction == "down":
            pullback = strokes[k + 1]
            pullback_low = pullback.low
            pullback_bars = pullback.bar_count
            status = "daily_pullback"
            if (
                k + 2 < len(strokes)
                and strokes[k + 2].direction == "up"
                and strokes[k + 2].high > pullback.high
            ):
                status = "second_launch"
        else:
            bottom_fx = next(
                (
                    fx
                    for fx in fractals
                    if fx.m_idx > leave.end.m_idx and fx.kind == "bottom"
                ),
                None,
            )
            if bottom_fx is not None:
                pullback_low = bottom_fx.price
                pullback_bars = bottom_fx.idx - leave.end_idx + 1
                status = "pullback_above_zg"
            else:
                status = "breakout_only"
                pullback_low = low_since
                pullback_bars = len(post)

        if status == "pullback_above_zg" and pullback_bars < cfg.min_pullback_bars:
            # 回抽K线太少，先不当作有效回抽
            status = "breakout_only"

        # 如果回抽结构出现过，再确认一次低点
        if pullback_low is not None and pullback_low <= pivot.zg * (
            1.0 + cfg.pullback_tolerance
        ):
            continue

        bottom_div = False
        if pullback is not None:
            bottom_div = bottom_divergence(prev_down, pullback, dif, hist)
        if cfg.require_bottom_divergence and not bottom_div:
            continue

        # 突破量能
        vol_window = bars[max(0, leave.end_idx - 20) : leave.end_idx]
        avg_vol = float(np.mean([b.volume for b in vol_window])) if vol_window else 0.0
        breakout_vol = bars[leave.end_idx].volume
        volume_ratio = breakout_vol / avg_vol if avg_vol > 0 else 0.0

        breakout_pct = leave.high / pivot.zg - 1.0
        pullback_low_vs_zg_pct = (
            pullback_low / pivot.zg - 1.0 if pullback_low is not None else None
        )
        chase_pct = last_bar.close / leave.high - 1.0
        stop_zg = pivot.zg * (1.0 - cfg.stop_buffer_pct)
        stop_pullback = (
            pullback_low * (1.0 - cfg.stop_buffer_pct * 0.5)
            if pullback_low is not None
            else None
        )

        score = _score(
            strength_ratio=strength_ratio,
            pullback_status=status,
            pullback_low=pullback_low,
            zg=pivot.zg,
            bottom_div=bottom_div,
            days_since_breakout=last_idx - leave.end_idx,
            volume_ratio=volume_ratio,
            chase_pct=chase_pct,
            top_div=top_div,
        )

        candidates.append(
            ScreenResult(
                ts_code=ts_code,
                name=name,
                industry=industry,
                close=last_bar.close,
                pivot_zg=pivot.zg,
                pivot_zd=pivot.zd,
                pivot_gg=pivot.gg,
                pivot_dd=pivot.dd,
                pivot_start=pivot.start_date,
                pivot_end=pivot.end_date,
                pivot_bars=pivot.bar_count,
                pivot_width_pct=pivot.width_pct,
                breakout_date=leave.end.date,
                breakout_high=leave.high,
                breakout_pct=breakout_pct,
                strength_ratio=strength_ratio,
                top_divergence=top_div,
                pullback_status=status,
                pullback_low=pullback_low,
                pullback_bars=pullback_bars,
                pullback_low_vs_zg_pct=pullback_low_vs_zg_pct,
                bottom_divergence=bottom_div,
                days_since_breakout=last_idx - leave.end_idx,
                volume_ratio=volume_ratio,
                chase_pct=chase_pct,
                stop_zg=stop_zg,
                stop_pullback=stop_pullback,
                score=score,
                market_ok=market_ok,
            )
        )

    if not candidates:
        return None

    return max(candidates, key=lambda item: item.score)
