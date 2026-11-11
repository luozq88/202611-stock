"""资金流与换手率的截面过滤。

Tushare 的 `moneyflow_dc`（东财大单净占比）需要 5000 积分以上，多数账号
没有权限；这里改用全市场都有的 `moneyflow`，用“大单/超大单买卖金额”
除以当日成交额，换算出同样含义的净流入占比。

单位换算：
- `moneyflow.*_amount` 为万元；
- `daily.amount` 为千元，乘以 0.1 得到万元（1 千元 = 0.1 万元）。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def prepare_moneyflow(mf: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """把 moneyflow 的绝对额换算成大单/超大单净流入占比（%）。"""
    columns = [
        "ts_code",
        "trade_date",
        "lg_net_rate",
        "elg_net_rate",
        "main_net_rate",
        "main_net_amount",
    ]
    if mf is None or mf.empty or daily is None or daily.empty:
        return pd.DataFrame(columns=columns)

    amount = daily[["ts_code", "trade_date", "amount"]].copy()
    amount["trade_date"] = amount["trade_date"].astype(str)
    mf = mf.copy()
    mf["trade_date"] = mf["trade_date"].astype(str)
    df = mf.merge(amount, on=["ts_code", "trade_date"], how="inner")
    if df.empty:
        return pd.DataFrame(columns=columns)

    num_cols = [
        "buy_lg_amount",
        "sell_lg_amount",
        "buy_elg_amount",
        "sell_elg_amount",
        "amount",
    ]
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    amount_wan = df["amount"] * 0.1
    lg_net = df["buy_lg_amount"] - df["sell_lg_amount"]
    elg_net = df["buy_elg_amount"] - df["sell_elg_amount"]

    def _rate(net: pd.Series) -> pd.Series:
        return np.divide(
            net.to_numpy(dtype="float64") * 100.0,
            amount_wan.to_numpy(dtype="float64"),
            out=np.zeros(len(df), dtype="float64"),
            where=amount_wan.to_numpy(dtype="float64") > 0,
        )

    df["lg_net_rate"] = _rate(lg_net)
    df["elg_net_rate"] = _rate(elg_net)
    df["main_net_rate"] = df["lg_net_rate"] + df["elg_net_rate"]
    df["main_net_amount"] = lg_net + elg_net
    return df[columns]


def moneyflow_filter(
    rates: pd.DataFrame,
    recent_days: int = 10,
    moderate_max: float = 5.0,
    spike_max: float = 12.0,
    continuity: float = 0.6,
) -> pd.DataFrame:
    """截面过滤：大单净流入“温和、连续、无单日暴入”。

    通过条件：
    1. 至少 60% 的交易日净流入占比落在 [0, moderate_max]；
    2. 任何一天净流入占比的绝对值都小于 spike_max；
    3. 区间平均净流入为正。
    """
    columns = ["ts_code", "mf_pass", "mf_mean_rate", "mf_continuity", "mf_max_abs"]
    if rates is None or rates.empty:
        return pd.DataFrame(columns=columns)

    df = rates.sort_values(["ts_code", "trade_date"]).copy()
    df = df.groupby("ts_code", group_keys=False, sort=False).tail(recent_days)
    min_days = max(5, int(np.ceil(recent_days * 0.6)))

    records = []
    for ts_code, group in df.groupby("ts_code", sort=False):
        values = group["main_net_rate"].dropna()
        if len(values) < min_days:
            records.append(
                {
                    "ts_code": ts_code,
                    "mf_pass": False,
                    "mf_mean_rate": float(values.mean()) if len(values) else 0.0,
                    "mf_continuity": 0.0,
                    "mf_max_abs": float(values.abs().max()) if len(values) else 0.0,
                }
            )
            continue
        moderate = ((values >= 0) & (values <= moderate_max)).mean()
        max_abs = float(values.abs().max())
        mean_rate = float(values.mean())
        passed = moderate >= continuity and max_abs < spike_max and mean_rate > 0
        records.append(
            {
                "ts_code": ts_code,
                "mf_pass": bool(passed),
                "mf_mean_rate": mean_rate,
                "mf_continuity": float(moderate),
                "mf_max_abs": max_abs,
            }
        )
    return pd.DataFrame(records, columns=columns)


def turnover_filter(
    basic: pd.DataFrame,
    recent_days: int = 20,
    turnover_min: float = 2.0,
    turnover_max: float = 8.0,
    tail_min: float = 1.0,
) -> pd.DataFrame:
    """换手率温和区间：中位数在 [turnover_min, turnover_max]，且近期不萎缩。"""
    columns = ["ts_code", "turnover_pass", "turnover_median", "turnover_tail"]
    if basic is None or basic.empty:
        return pd.DataFrame(columns=columns)

    df = basic.copy()
    if "turnover_rate_f" in df.columns:
        df["turnover_rate_f"] = pd.to_numeric(df["turnover_rate_f"], errors="coerce")
    else:
        df["turnover_rate_f"] = np.nan
    df["turnover_rate"] = pd.to_numeric(df.get("turnover_rate"), errors="coerce")
    df["turnover"] = df["turnover_rate_f"].fillna(df["turnover_rate"])

    df = df.dropna(subset=["turnover"])
    df = df.sort_values(["ts_code", "trade_date"]).groupby(
        "ts_code", group_keys=False, sort=False
    ).tail(recent_days)

    records = []
    for ts_code, group in df.groupby("ts_code", sort=False):
        values = group["turnover"]
        if len(values) < max(5, recent_days // 2):
            records.append(
                {
                    "ts_code": ts_code,
                    "turnover_pass": False,
                    "turnover_median": float(values.median()) if len(values) else 0.0,
                    "turnover_tail": float(values.tail(5).mean()) if len(values) else 0.0,
                }
            )
            continue
        median = float(values.median())
        tail = float(values.tail(5).mean())
        passed = turnover_min <= median <= turnover_max and tail > tail_min
        records.append(
            {
                "ts_code": ts_code,
                "turnover_pass": bool(passed),
                "turnover_median": median,
                "turnover_tail": tail,
            }
        )
    return pd.DataFrame(records, columns=columns)


def select_panels(
    data,
    trade_dates: list[str],
    need_basic: bool = True,
    logger: Optional[object] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """按交易日拉取 daily / daily_basic / moneyflow 三个截面并拼接。"""
    daily_frames, basic_frames, mf_frames = [], [], []
    total = len(trade_dates)
    for order, day in enumerate(trade_dates, start=1):
        daily_frames.append(data.get_panel("daily", day))
        if need_basic:
            basic_frames.append(
                data.get_panel(
                    "daily_basic",
                    day,
                    fields="ts_code,trade_date,turnover_rate,turnover_rate_f",
                )
            )
        mf_frames.append(data.get_panel("moneyflow", day))
        if logger is not None and (order % 10 == 0 or order == total):
            logger.info("截面数据进度 %s/%s", order, total)

    def _concat(frames):
        frames = [f for f in frames if f is not None and not f.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    return _concat(daily_frames), _concat(basic_frames), _concat(mf_frames)
