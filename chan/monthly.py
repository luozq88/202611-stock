"""月线趋势过滤（含月线 MACD 零轴判断）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

from .macd import macd_series
from .structure import Bar


@dataclass(slots=True)
class MonthlyTrend:
    ok: bool
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    close: Optional[float] = None
    dif: Optional[float] = None
    dea: Optional[float] = None
    ma_bull: bool = False
    close_above_ma20: bool = False
    macd_above_zero: bool = False
    months: int = 0
    last_month: str = ""


def _resample(series: pd.Series, rule: str) -> pd.Series:
    """按周期重采样；兼容 pandas 3.x 的 "ME" 与旧版本的 "M"。"""
    try:
        return series.resample(rule).last()
    except ValueError:
        legacy = {"ME": "M"}.get(rule, rule)
        return series.resample(legacy).last()


def resample_close(bars: Sequence[Bar], rule: str = "ME") -> pd.Series:
    """把日线收盘价合成为指定周期的收盘价序列。"""
    if not bars:
        return pd.Series(dtype="float64")

    dates = pd.to_datetime([b.date for b in bars], format="%Y%m%d", errors="coerce")
    closes = pd.Series([b.close for b in bars], dtype="float64")
    frame = pd.DataFrame({"date": dates, "close": closes}).dropna(subset=["date"])
    if frame.empty:
        return pd.Series(dtype="float64")

    frame = frame.sort_values("date")
    return _resample(frame.set_index("date")["close"], rule).dropna()


def monthly_trend(
    bars: Sequence[Bar],
    min_months: int = 24,
    include_current: bool = True,
    require_macd_zero: bool = True,
    min_months_for_macd: int = 36,
) -> MonthlyTrend:
    """用日线收盘价合成月线，判断月线多头 + 月线 MACD 零轴上方。

    多头排列：MA5 > MA10 > MA20，且月线收盘价在 MA20 上方。
    MACD：DIF > 0 且 DEA > 0，表示月线级别处于强势区。
    include_current=True 时包含当月尚未走完的月K，更贴近实时状态。
    """
    if len(bars) < min_months:
        return MonthlyTrend(False)

    monthly = resample_close(bars, "ME")
    if not include_current and len(monthly) > 0:
        last_dt = pd.to_datetime(bars[-1].date, format="%Y%m%d", errors="coerce")
        if pd.notna(last_dt) and monthly.index[-1].to_period("M") == last_dt.to_period("M"):
            monthly = monthly.iloc[:-1]

    months = len(monthly)
    if months < min_months:
        return MonthlyTrend(False, months=months)

    ma5 = float(monthly.iloc[-5:].mean())
    ma10 = float(monthly.iloc[-10:].mean())
    ma20 = float(monthly.iloc[-20:].mean())
    close = float(monthly.iloc[-1])
    ma_bull = ma5 > ma10 > ma20
    close_above = close > ma20

    dif_value: Optional[float] = None
    dea_value: Optional[float] = None
    macd_ok = False
    if months >= min_months_for_macd:
        dif, dea, _ = macd_series(monthly.reset_index(drop=True))
        dif_value = float(dif.iloc[-1])
        dea_value = float(dea.iloc[-1])
        macd_ok = dif_value > 0 and dea_value > 0

    ok = ma_bull and close_above and (macd_ok or not require_macd_zero)
    return MonthlyTrend(
        ok=ok,
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        close=close,
        dif=dif_value,
        dea=dea_value,
        ma_bull=ma_bull,
        close_above_ma20=close_above,
        macd_above_zero=macd_ok,
        months=months,
        last_month=monthly.index[-1].strftime("%Y-%m"),
    )
