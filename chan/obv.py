"""OBV / A-D 的“价横量升”判断。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .structure import Bar


@dataclass(slots=True)
class VolumeSignal:
    ok: bool
    price_change: float = 0.0
    obv_slope: float = 0.0
    ad_slope: float = 0.0


def calc_obv_ad(bars: Sequence[Bar]) -> tuple[np.ndarray, np.ndarray]:
    """计算 OBV 与 A/D Line 的累计值。"""
    close = np.array([b.close for b in bars], dtype="float64")
    high = np.array([b.high for b in bars], dtype="float64")
    low = np.array([b.low for b in bars], dtype="float64")
    volume = np.array([b.volume for b in bars], dtype="float64")

    obv = np.zeros(len(close), dtype="float64")
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            obv[i] = obv[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            obv[i] = obv[i - 1] - volume[i]
        else:
            obv[i] = obv[i - 1]

    span = high - low
    clv = np.divide(
        (close - low) - (high - close),
        span,
        out=np.zeros_like(close),
        where=span != 0,
    )
    ad = np.cumsum(clv * volume)
    return obv, ad


def obv_divergence(
    bars: Sequence[Bar],
    lookback: int = 25,
    price_flat_max: float = 0.08,
    slope_min: float = 0.05,
) -> VolumeSignal:
    """价格横盘/缓涨，但 OBV 或 A/D 斜率向上。

    斜率用期间平均成交量归一化，消除不同股票量纲差异。
    """
    if len(bars) < lookback:
        return VolumeSignal(False)

    recent = list(bars[-lookback:])
    obv, ad = calc_obv_ad(recent)
    close = np.array([b.close for b in recent], dtype="float64")
    volume = np.array([b.volume for b in recent], dtype="float64")

    price_change = float(close[-1] / close[0] - 1.0) if close[0] > 0 else 0.0
    price_flat = price_change < price_flat_max

    x = np.arange(len(recent), dtype="float64")
    obv_slope = float(np.polyfit(x, obv, 1)[0])
    ad_slope = float(np.polyfit(x, ad, 1)[0])

    vol_mean = float(volume.mean())
    if vol_mean > 0:
        obv_norm = obv_slope / vol_mean
        ad_norm = ad_slope / vol_mean
    else:
        obv_norm = ad_norm = 0.0

    ok = price_flat and (obv_norm > slope_min or ad_norm > slope_min)
    return VolumeSignal(
        ok=ok,
        price_change=price_change,
        obv_slope=obv_norm,
        ad_slope=ad_norm,
    )
