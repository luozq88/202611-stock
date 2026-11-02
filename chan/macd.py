"""MACD 与背驰判断。"""

from __future__ import annotations

import pandas as pd

from .structure import Stroke


def macd_series(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
):
    """返回 DIF、DEA、MACD 柱（国内习惯：柱=2*(DIF-DEA)）。"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = 2.0 * (dif - dea)
    return dif, dea, hist


def _segment_stats(dif: pd.Series, hist: pd.Series, start: int, end: int) -> dict:
    start = max(int(start), 0)
    end = max(int(end), start)
    seg_dif = dif.iloc[start : end + 1]
    seg_hist = hist.iloc[start : end + 1]
    pos = seg_hist[seg_hist > 0]
    neg = seg_hist[seg_hist < 0]
    return {
        "dif_max": float(seg_dif.max()) if len(seg_dif) else 0.0,
        "dif_min": float(seg_dif.min()) if len(seg_dif) else 0.0,
        "hist_pos": float(pos.sum()) if len(pos) else 0.0,
        "hist_neg": float(neg.sum()) if len(neg) else 0.0,
    }


def top_divergence(
    prev_up: Stroke,
    cur_up: Stroke,
    dif: pd.Series,
    hist: pd.Series,
) -> bool:
    """顶背驰：价格创新高，但 DIF 高点更低或红柱面积明显缩小。"""
    if cur_up.high <= prev_up.high:
        return False

    prev = _segment_stats(dif, hist, prev_up.start_idx, prev_up.end_idx)
    cur = _segment_stats(dif, hist, cur_up.start_idx, cur_up.end_idx)

    if prev["dif_max"] <= 0 and cur["dif_max"] <= 0:
        return False

    dif_lower = cur["dif_max"] < prev["dif_max"] * 0.95
    area_lower = prev["hist_pos"] > 0 and cur["hist_pos"] < prev["hist_pos"] * 0.8
    return dif_lower or area_lower


def bottom_divergence(
    prev_down: Stroke,
    cur_down: Stroke,
    dif: pd.Series,
    hist: pd.Series,
) -> bool:
    """底背驰：价格创新低，但 DIF 低点更高或绿柱面积明显缩小。"""
    if cur_down.low >= prev_down.low:
        return False

    prev = _segment_stats(dif, hist, prev_down.start_idx, prev_down.end_idx)
    cur = _segment_stats(dif, hist, cur_down.start_idx, cur_down.end_idx)

    area_smaller = prev["hist_neg"] < 0 and abs(cur["hist_neg"]) < abs(prev["hist_neg"]) * 0.8
    dif_higher = cur["dif_min"] > prev["dif_min"]
    return area_smaller or dif_higher
