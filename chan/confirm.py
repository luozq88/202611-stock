"""次级别（30分钟/5分钟）回抽确认。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from .macd import macd_series


def confirm_minute_pullback(
    df: pd.DataFrame,
    zg: float,
    breakout_date: str,
    min_bars: int = 3,
    breakout_high: Optional[float] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """用分钟线确认日线突破后的回抽是否有效。

    条件：
    1. 找到突破日之后的分钟高点；
    2. 高点之后的回抽低点仍在日线中枢上沿 ZG 之上；
    3. 回抽至少有 min_bars 根分钟K线；
    4. 回抽低点之后出现 MACD 金叉（底分型/底背驰的简化替代）。
    """
    if df is None or df.empty:
        return False, "无分钟数据", {}

    data = df.copy()
    if "trade_time" in data.columns:
        data["dt"] = pd.to_datetime(data["trade_time"], errors="coerce")
    elif "trade_date" in data.columns:
        data["dt"] = pd.to_datetime(data["trade_date"], errors="coerce")
    else:
        return False, "分钟数据缺少时间列", {}

    data = data.dropna(subset=["dt"]).sort_values("dt").reset_index(drop=True)
    if data.empty:
        return False, "分钟数据为空", {}

    start = pd.to_datetime(breakout_date, errors="coerce")
    if pd.isna(start):
        return False, "突破日期无法解析", {}

    post = data[data["dt"].dt.normalize() >= start].reset_index(drop=True)
    if len(post) < min_bars:
        return False, "突破后分钟K线不足", {"pull_low": None, "pull_bars": len(post)}

    # 优先锚定突破日当天的最高点，避免把后面的二次启动高点误当成突破高点
    if breakout_high is not None:
        day_bars = post[post["dt"].dt.normalize() == start]
        if not day_bars.empty:
            high_pos = int(day_bars["high"].idxmax())
        else:
            reached = post.index[post["high"] >= breakout_high * 0.98]
            high_pos = int(reached[0]) if len(reached) else int(post["high"].idxmax())
    else:
        high_pos = int(post["high"].idxmax())

    after_high = post.iloc[high_pos + 1 :].reset_index(drop=True)
    if len(after_high) < min_bars:
        return (
            False,
            "突破后回抽分钟K线不足",
            {"pull_low": None, "pull_bars": len(after_high)},
        )

    pull_low = float(after_high["low"].min())
    low_idx_in_after = int(after_high["low"].idxmin())
    pull_bars = low_idx_in_after + 1
    struct_ok = pull_low > zg and pull_bars >= min_bars

    dif, dea, _ = macd_series(post["close"].astype("float64"))

    low_pos = high_pos + 1 + low_idx_in_after
    golden = False
    for i in range(max(1, low_pos + 1), len(post)):
        if dif.iloc[i] > dea.iloc[i] and dif.iloc[i - 1] <= dea.iloc[i - 1]:
            golden = True
            break

    macd_ok = bool(golden)
    ok = bool(struct_ok and macd_ok)

    note = (
        f"回抽低点 {pull_low:.2f} / ZG {zg:.2f}，{pull_bars} 根；"
        f"结构{'通过' if struct_ok else '不通过'}，"
        f"MACD金叉{'是' if macd_ok else '否'}"
    )
    return ok, note, {
        "pull_low": pull_low,
        "pull_bars": pull_bars,
        "struct_ok": struct_ok,
        "macd_ok": macd_ok,
    }
