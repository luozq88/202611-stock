"""周线非暴涨过滤。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .monthly import resample_close
from .structure import Bar


@dataclass(slots=True)
class WeeklyTrend:
    ok: bool                       # True 表示未暴涨、可以参与
    close: Optional[float] = None
    ma10: Optional[float] = None
    deviation: Optional[float] = None  # 周收盘 / 周MA10
    weeks: int = 0


def weekly_not_surged(
    bars: Sequence[Bar],
    ratio: float = 1.3,
    min_weeks: int = 12,
    ma_period: int = 10,
) -> WeeklyTrend:
    """周线收盘价不超过 10 周均线的 ratio 倍。

    对应“错过低位、但不追已经暴涨的票”：当周收盘偏离 10 周均线
    超过 30%（ratio=1.3）时视为短期涨幅过大，直接排除。
    """
    weekly = resample_close(bars, "W-FRI")
    weeks = len(weekly)
    need = max(min_weeks, ma_period)
    if weeks < need:
        return WeeklyTrend(False, weeks=weeks)

    ma10 = float(weekly.iloc[-ma_period:].mean())
    close = float(weekly.iloc[-1])
    deviation = close / ma10 if ma10 > 0 else None
    ok = deviation is not None and deviation < ratio
    return WeeklyTrend(
        ok=ok,
        close=close,
        ma10=ma10,
        deviation=deviation,
        weeks=weeks,
    )
