"""分层漏斗选股：截面粗筛 -> 月线/周线 -> 逐股结构精筛。"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import List, Optional, Sequence

import pandas as pd

from .config import ChanConfig
from .confirm import confirm_minute_pullback
from .data import bars_from_frame
from .fundflow import (
    moneyflow_filter,
    prepare_moneyflow,
    select_panels,
    turnover_filter,
)
from .monthly import monthly_trend
from .obv import obv_divergence
from .screener import ScreenResult, analyze_stock
from .structure import build_strokes, find_fractals, find_pivots, merge_bars
from .weekly import weekly_not_surged

STAGE_ORDER = {
    "breakout_only": 0,
    "pullback_above_zg": 1,
    "daily_pullback": 2,
    "second_launch": 3,
}

POOL_ORDER = {"signal": 0, "breakout_watch": 1, "buy_watch": 2, "left_watch": 3}


@dataclass(slots=True)
class FunnelRow:
    ts_code: str
    name: str
    industry: str
    pool: str
    close: float = 0.0

    monthly_ok: Optional[bool] = None
    monthly_ma5: Optional[float] = None
    monthly_ma10: Optional[float] = None
    monthly_ma20: Optional[float] = None
    monthly_dif: Optional[float] = None
    monthly_dea: Optional[float] = None
    monthly_macd_ok: Optional[bool] = None

    weekly_ok: Optional[bool] = None
    weekly_deviation: Optional[float] = None

    obv_ok: Optional[bool] = None
    obv_price_change: Optional[float] = None
    obv_slope: Optional[float] = None
    ad_slope: Optional[float] = None

    mf_pass: Optional[bool] = None
    mf_mean_rate: Optional[float] = None
    mf_recent_rate: Optional[float] = None
    mf_continuity: Optional[float] = None
    mf_max_abs: Optional[float] = None

    turnover_pass: Optional[bool] = None
    turnover_median: Optional[float] = None
    turnover_tail: Optional[float] = None

    position_drawdown: Optional[float] = None
    has_zs: bool = False
    pivot_zg: Optional[float] = None
    pivot_zd: Optional[float] = None
    pullback_status: str = ""
    breakout_date: str = ""
    volume_ratio: Optional[float] = None
    volume_tag: str = ""
    top_divergence: Optional[bool] = None
    score: float = 0.0

    sub_ok: Optional[bool] = None
    sub_note: str = ""


def funnel_columns() -> List[str]:
    return [item.name for item in fields(FunnelRow)]


def _bool_or_none(value) -> Optional[bool]:
    if value is None or pd.isna(value):
        return None
    return bool(value)


def _float_or_none(value) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _volume_tag(ratio: Optional[float]) -> str:
    if ratio is None or pd.isna(ratio):
        return ""
    if ratio >= 1.2:
        return "放量"
    if ratio >= 0.8:
        return "平量"
    return "缩量"


def _attach_signals(
    row: FunnelRow, result: ScreenResult, pool: str = "signal"
) -> FunnelRow:
    # 突破首日尚未回抽，不满足三买的必要条件，单独归入“待回抽”
    if result.pullback_status == "breakout_only":
        pool = "breakout_watch"
    row.pool = pool
    row.close = result.close
    row.has_zs = True
    row.pivot_zg = result.pivot_zg
    row.pivot_zd = result.pivot_zd
    row.pullback_status = result.pullback_status
    row.breakout_date = result.breakout_date
    row.volume_ratio = result.volume_ratio
    row.volume_tag = _volume_tag(result.volume_ratio)
    row.top_divergence = result.top_divergence
    row.score = result.score
    return row


def screen_funnel(
    data,
    universe: pd.DataFrame,
    cfg: ChanConfig,
    end_date: str,
    start_date: str,
    *,
    panel_days: int = 30,
    min_stage: str = "breakout_only",
    confirm_30m: bool = False,
    confirm_top: int = 30,
    logger=None,
) -> List[FunnelRow]:
    """执行完整漏斗，返回分层结果。

    顺序上把截面资金/换手过滤提前，是因为它只需要几十次全市场请求，
    可以先砍掉大部分标的，再对剩余股票逐只拉日线，成本更低。
    """
    log = logger.info if logger is not None else (lambda *a, **k: None)

    trade_dates = data.get_trade_dates(panel_days, end_date)
    if not trade_dates:
        raise RuntimeError("无法获取交易日历")
    log("截面区间：%s ~ %s（%s 个交易日）", trade_dates[0], trade_dates[-1], len(trade_dates))

    daily_panel, basic_panel, mf_panel = select_panels(
        data,
        trade_dates,
        need_basic=cfg.require_turnover_pass,
        logger=logger,
    )
    log(
        "截面数据：daily=%s / daily_basic=%s / moneyflow=%s",
        len(daily_panel),
        len(basic_panel),
        len(mf_panel),
    )

    rates = prepare_moneyflow(mf_panel, daily_panel)
    mf_stats = moneyflow_filter(
        rates,
        recent_days=cfg.mf_recent_days,
        moderate_max=cfg.mf_moderate_max,
        spike_max=cfg.mf_spike_max,
        continuity=cfg.mf_continuity,
        veto_days=cfg.mf_veto_days,
        veto_threshold=cfg.mf_veto_threshold,
        veto_hits=cfg.mf_veto_hits,
        decay=cfg.mf_decay,
    )
    turnover_stats = turnover_filter(
        basic_panel,
        recent_days=cfg.turnover_recent_days,
        turnover_min=cfg.turnover_min,
        turnover_max=cfg.turnover_max,
        tail_min=cfg.turnover_tail_min,
    )

    pool = universe.merge(mf_stats, on="ts_code", how="left")
    if "mf_pass" not in pool.columns:
        pool["mf_pass"] = False
        pool["mf_mean_rate"] = None
        pool["mf_continuity"] = None
        pool["mf_max_abs"] = None
    if not turnover_stats.empty:
        pool = pool.merge(turnover_stats, on="ts_code", how="left")
    else:
        pool["turnover_pass"] = False
        pool["turnover_median"] = None
        pool["turnover_tail"] = None

    before = len(pool)
    if cfg.require_fund_pass:
        pool = pool[pool["mf_pass"].fillna(False).astype(bool)]
    if cfg.require_turnover_pass:
        pool = pool[pool["turnover_pass"].fillna(False).astype(bool)]
    log("截面粗筛：%s -> %s 只", before, len(pool))

    signal_cfg = replace(cfg, require_monthly_bull=False)

    rows: List[FunnelRow] = []
    errors = 0
    total = len(pool)
    min_rank = STAGE_ORDER.get(min_stage, 0)
    stats = {
        "no_data": 0,
        "stale": 0,
        "short_bars": 0,
        "position_fail": 0,
        "weekly_fail": 0,
        "monthly_fail": 0,
        "obv_fail": 0,
        "signal": 0,
        "breakout_watch": 0,
        "watch": 0,
    }

    for order, record in enumerate(pool.to_dict("records"), start=1):
        ts_code = str(record["ts_code"])
        name = str(record.get("name") or ts_code)
        industry = str(record.get("industry") or "")
        if pd.isna(record.get("name")):
            name = ts_code

        try:
            df = data.get_daily(ts_code, start_date, end_date)
            if df is None or df.empty:
                stats["no_data"] += 1
                continue
            bars = bars_from_frame(df)
            if bars[-1].date < end_date:
                # 允许最后一天停牌，但超过 15 个自然日视为陈旧数据
                if pd.Timestamp(bars[-1].date) < pd.Timestamp(end_date) - pd.Timedelta(days=15):
                    stats["stale"] += 1
                    continue
            if len(bars) < cfg.min_bars:
                stats["short_bars"] += 1
                continue

            window = bars[-cfg.position_lookback :]
            high = max(b.high for b in window)
            drawdown = bars[-1].close / high - 1.0 if high > 0 else -1.0
            if cfg.require_position_filter and drawdown < cfg.position_max_drawdown:
                stats["position_fail"] += 1
                continue

            weekly = weekly_not_surged(
                bars,
                ratio=cfg.max_weekly_deviation,
                min_weeks=cfg.min_weekly_bars,
            )
            if cfg.require_not_surged and not weekly.ok:
                stats["weekly_fail"] += 1
                continue

            monthly = monthly_trend(
                bars,
                min_months=cfg.monthly_ma_min_months,
                include_current=cfg.monthly_include_current,
                require_macd_zero=cfg.monthly_require_macd_zero,
                min_months_for_macd=cfg.monthly_min_months_for_macd,
            )
            if cfg.require_monthly_bull and not monthly.ok:
                stats["monthly_fail"] += 1
                continue

            volume = obv_divergence(
                bars,
                lookback=cfg.obv_lookback,
                price_flat_max=cfg.obv_price_flat_max,
                slope_min=cfg.obv_slope_min,
            )
            if cfg.require_obv_pass and not volume.ok:
                stats["obv_fail"] += 1
                continue

            row = FunnelRow(
                ts_code=ts_code,
                name=name,
                industry=industry,
                pool="left_watch",
                close=bars[-1].close,
                monthly_ok=monthly.ok,
                monthly_ma5=monthly.ma5,
                monthly_ma10=monthly.ma10,
                monthly_ma20=monthly.ma20,
                monthly_dif=monthly.dif,
                monthly_dea=monthly.dea,
                monthly_macd_ok=monthly.macd_above_zero,
                weekly_ok=weekly.ok,
                weekly_deviation=weekly.deviation,
                obv_ok=volume.ok,
                obv_price_change=volume.price_change,
                obv_slope=volume.obv_slope,
                ad_slope=volume.ad_slope,
                mf_pass=_bool_or_none(record.get("mf_pass")),
                mf_mean_rate=_float_or_none(record.get("mf_mean_rate")),
                mf_recent_rate=_float_or_none(record.get("mf_recent_rate")),
                mf_continuity=_float_or_none(record.get("mf_continuity")),
                mf_max_abs=_float_or_none(record.get("mf_max_abs")),
                turnover_pass=_bool_or_none(record.get("turnover_pass")),
                turnover_median=_float_or_none(record.get("turnover_median")),
                turnover_tail=_float_or_none(record.get("turnover_tail")),
                position_drawdown=drawdown,
            )

            result = analyze_stock(
                ts_code=ts_code,
                name=name,
                industry=industry,
                bars=bars,
                cfg=signal_cfg,
            )
            if result is not None and STAGE_ORDER.get(result.pullback_status, 0) >= min_rank:
                attached = _attach_signals(row, result, pool="signal")
                rows.append(attached)
                if attached.pool == "breakout_watch":
                    stats["breakout_watch"] += 1
                else:
                    stats["signal"] += 1
            else:
                merged = merge_bars(bars)
                fractals = find_fractals(merged)
                strokes = build_strokes(fractals, cfg.min_stroke_gap)
                pivots = find_pivots(
                    strokes, cfg.min_pivot_width_pct, cfg.min_pivot_bars
                )
                row.has_zs = bool(pivots)
                if pivots:
                    row.pivot_zg = pivots[-1].zg
                    row.pivot_zd = pivots[-1].zd
                near_zg = (
                    row.pivot_zg is not None
                    and row.close >= row.pivot_zg * cfg.watch_zg_ratio
                )
                row.pool = "buy_watch" if near_zg else "left_watch"
                rows.append(row)
                stats["watch"] += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            if errors <= 5:
                log("%s 分析失败：%s", ts_code, exc)

        if order % 100 == 0 or order == total:
            log("逐股精筛进度 %s/%s，入选 %s，错误 %s", order, total, len(rows), errors)

    log(
        "过滤明细：无数据 %s，停牌/陈旧 %s，数据不足 %s，位置过深 %s，周线暴涨 %s，月线不符 %s，量能不达 %s",
        stats["no_data"],
        stats["stale"],
        stats["short_bars"],
        stats["position_fail"],
        stats["weekly_fail"],
        stats["monthly_fail"],
        stats["obv_fail"],
    )

    if confirm_30m and rows:
        signals = [row for row in rows if row.pool == "signal"]
        signals.sort(key=lambda item: item.score, reverse=True)
        count = min(confirm_top, len(signals))
        log("开始 30 分钟回抽确认：前 %s 只", count)
        for row in signals[:count]:
            try:
                minute_df = data.get_minutes(
                    row.ts_code, "30min", row.breakout_date, end_date
                )
                ok, note, _ = confirm_minute_pullback(
                    minute_df,
                    row.pivot_zg,
                    row.breakout_date,
                    min_bars=cfg.min_pullback_bars,
                    breakout_high=None,
                )
                row.sub_ok = ok
                row.sub_note = note
            except Exception as exc:  # noqa: BLE001
                row.sub_ok = None
                row.sub_note = f"分钟确认失败：{exc}"

    rows.sort(
        key=lambda item: (
            POOL_ORDER.get(item.pool, 9),
            -item.score,
            -(item.mf_mean_rate or 0.0),
        )
    )
    return rows


def rows_to_dataframe(rows: Sequence[FunnelRow]) -> pd.DataFrame:
    columns = funnel_columns()
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {field: getattr(row, field) for field in columns}
            for row in rows
        ],
        columns=columns,
    )
