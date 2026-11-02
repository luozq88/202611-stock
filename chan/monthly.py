"""月线趋势过滤。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

from .structure import Bar


@dataclass(slots=True)
class MonthlyTrend:
    ok: bool
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    months: int = 0
    last_month: str = ""


def _resample_monthly(series: pd.Series) -> pd.Series:
    # pandas 3.x 使用 "ME"，旧版本使用 "M"
    try:
        return series.resample("ME").last()
    except ValueError:
        return series.resample("M").last()


def monthly_trend(
    bars: Sequence[Bar],
    min_months: int = 20,
    include_current: bool = True,
) -> MonthlyTrend:
    """用日线收盘价合成月线，判断 MA5 > MA10 > MA20。

    include_current=True 时包含当月尚未走完的月K（用最新收盘价），
    更贴近“最近一个月”的实时状态；设为 False 则只用最近一个完整月。
    """
    if len(bars) < min_months:
        return MonthlyTrend(False)

    dates = pd.to_datetime([b.date for b in bars], format="%Y%m%d", errors="coerce")
    closes = pd.Series([b.close for b in bars], dtype="float64")
    frame = pd.DataFrame({"date": dates, "close": closes}).dropna(subset=["date"])
    if frame.empty:
        return MonthlyTrend(False)

    frame = frame.sort_values("date")
    monthly = _resample_monthly(frame.set_index("date")["close"]).dropna()

    if not include_current and len(monthly) > 0:
        last_bar_month = frame["date"].iloc[-1].to_period("M")
        if monthly.index[-1].to_period("M") == last_bar_month:
            monthly = monthly.iloc[:-1]

    months = len(monthly)
    if months < min_months:
        return MonthlyTrend(False, months=months)

    ma5 = float(monthly.iloc[-5:].mean())
    ma10 = float(monthly.iloc[-10:].mean())
    ma20 = float(monthly.iloc[-20:].mean())
    return MonthlyTrend(
        ok=ma5 > ma10 > ma20,
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        months=months,
        last_month=monthly.index[-1].strftime("%Y-%m"),
    )
