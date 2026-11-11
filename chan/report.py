"""结果输出。"""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

from .screener import ScreenResult


def results_to_dataframe(results: Sequence[ScreenResult]) -> pd.DataFrame:
    columns = [field.name for field in fields(ScreenResult)]
    if not results:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([asdict(item) for item in results], columns=columns)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_markdown(
    df: pd.DataFrame, path: Path, top_n: int = 30, title: str = "缠论三买选股结果"
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {title}",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if df.empty:
        lines.append("本轮没有筛选到符合条件的标的。")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    display = df.head(top_n).copy()
    display["breakout_pct"] = (display["breakout_pct"] * 100).map(lambda x: f"{x:.1f}%")
    display["strength_ratio"] = display["strength_ratio"].map(lambda x: f"{x:.2f}")
    display["pullback_low_vs_zg_pct"] = display["pullback_low_vs_zg_pct"].map(
        lambda x: "-" if pd.isna(x) else f"{x * 100:.1f}%"
    )
    display["pivot_width_pct"] = display["pivot_width_pct"].map(lambda x: f"{x * 100:.1f}%")
    display["score"] = display["score"].map(lambda x: f"{x:.1f}")
    display["sub_ok"] = display["sub_ok"].map(
        lambda x: "未确认" if pd.isna(x) else ("是" if x else "否")
    )
    display["signal_mode"] = display["signal_mode"].map(
        lambda x: {"stroke": "笔结构", "bar": "进行中"}.get(x, x)
    )
    display["monthly_bull"] = display["monthly_bull"].map(
        lambda x: "-" if pd.isna(x) else ("是" if bool(x) else "否")
    )
    for col in (
        "close",
        "pivot_zg",
        "pivot_zd",
        "breakout_high",
        "pullback_low",
        "stop_zg",
        "stop_pullback",
    ):
        if col in display.columns:
            display[col] = display[col].map(
                lambda x: "-" if pd.isna(x) else f"{x:.2f}"
            )

    columns = [
        ("ts_code", "代码"),
        ("name", "名称"),
        ("industry", "行业"),
        ("close", "收盘"),
        ("pivot_zg", "中枢ZG"),
        ("pivot_zd", "中枢ZD"),
        ("breakout_date", "突破日"),
        ("breakout_pct", "突破幅度"),
        ("strength_ratio", "突破力度"),
        ("pullback_status", "回抽状态"),
        ("signal_mode", "信号类型"),
        ("monthly_bull", "月线多头"),
        ("pullback_low", "回抽低点"),
        ("pullback_bars", "回抽K线"),
        ("pullback_low_vs_zg_pct", "距ZG"),
        ("stop_zg", "止损ZG"),
        ("score", "评分"),
        ("sub_ok", "次级别"),
    ]

    header = "| " + " | ".join(label for _, label in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines.extend([header, sep])
    for _, row in display.iterrows():
        cells = []
        for key, _ in columns:
            value = row.get(key, "")
            if pd.isna(value):
                value = "-"
            cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "说明：",
            "- `breakout_only`：刚突破、尚未形成明确回抽，属于观察对象。",
            "- `pullback_above_zg`：日线回抽低点仍在 ZG 上方，等待次级别确认。",
            "- `daily_pullback`：日线已走出完整回抽笔且不碰 ZG。",
            "- `second_launch`：回抽后已出现向上笔，确定性更高但位置更高。",
            "- 月线多头：月线 MA5 > MA10 > MA20（默认含当月未完成K线）。",
            "- 评分只是排序参考，不构成投资建议。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_funnel_markdown(
    df: pd.DataFrame, path: Path, top_n: int = 50, title: str = "漏斗选股结果"
) -> None:
    """输出分层漏斗结果的 Markdown。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {title}",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if df.empty:
        lines.append("本轮没有标的通过漏斗。")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    pool_names = {
        "signal": "确认三买",
        "breakout_watch": "待回抽",
        "buy_watch": "右侧观察",
        "left_watch": "左侧观察",
    }
    display = df.head(top_n).copy()
    display["pool"] = display["pool"].map(lambda x: pool_names.get(x, x))
    display["score"] = display["score"].map(lambda x: f"{x:.1f}")
    for col in ("monthly_ma5", "monthly_ma10", "monthly_ma20", "monthly_dif", "monthly_dea"):
        if col in display.columns:
            display[col] = display[col].map(lambda x: "-" if pd.isna(x) else f"{x:.2f}")
    display["weekly_deviation"] = display["weekly_deviation"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.2f}"
    )
    display["monthly_macd_ok"] = display["monthly_macd_ok"].map(
        lambda x: "-" if pd.isna(x) else ("是" if bool(x) else "否")
    )
    display["mf_mean_rate"] = display["mf_mean_rate"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.2f}%"
    )
    display["mf_recent_rate"] = display["mf_recent_rate"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.2f}%"
    )
    display["position_drawdown"] = display["position_drawdown"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.2%}"
    )
    display["turnover_median"] = display["turnover_median"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.2f}%"
    )
    display["obv_slope"] = display["obv_slope"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.3f}"
    )
    display["ad_slope"] = display["ad_slope"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.3f}"
    )
    for col in ("close", "pivot_zg", "pivot_zd"):
        if col in display.columns:
            display[col] = display[col].map(lambda x: "-" if pd.isna(x) else f"{x:.2f}")
    display["sub_ok"] = display["sub_ok"].map(
        lambda x: "未确认" if pd.isna(x) else ("是" if bool(x) else "否")
    )
    display["top_divergence"] = display["top_divergence"].map(
        lambda x: "-" if pd.isna(x) else ("是" if bool(x) else "否")
    )

    def _volume_desc(row) -> str:
        tag = row.get("volume_tag") or ""
        ratio = row.get("volume_ratio")
        if not tag or pd.isna(ratio):
            return "-"
        return f"{tag} {ratio:.2f}"

    display["volume_desc"] = display.apply(_volume_desc, axis=1)

    columns = [
        ("ts_code", "代码"),
        ("name", "名称"),
        ("industry", "行业"),
        ("pool", "分层"),
        ("close", "收盘"),
        ("monthly_ma5", "月MA5"),
        ("monthly_ma10", "月MA10"),
        ("monthly_ma20", "月MA20"),
        ("monthly_macd_ok", "月MACD>0"),
        ("weekly_deviation", "周偏离"),
        ("obv_slope", "OBV斜率"),
        ("ad_slope", "AD斜率"),
        ("mf_mean_rate", "大单净占比"),
        ("mf_recent_rate", "近3日净占比"),
        ("position_drawdown", "距120日高"),
        ("turnover_median", "换手中位"),
        ("pivot_zg", "中枢ZG"),
        ("pullback_status", "回抽状态"),
        ("volume_desc", "量能"),
        ("top_divergence", "顶背驰"),
        ("score", "评分"),
        ("sub_ok", "次级别"),
    ]

    header = "| " + " | ".join(label for _, label in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines.extend([header, sep])
    for _, row in display.iterrows():
        cells = []
        for key, _ in columns:
            value = row.get(key, "")
            if pd.isna(value):
                value = "-"
            cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "## 分层说明",
            "",
            "- 确认三买：日线中枢向上突破、回抽不碰 ZG，已满足三买的必要条件。",
            "- 待回抽：突破已发生但尚未回抽，不构成三买；必须等回抽不碰 ZG 才可升级。",
            "- 右侧观察：已有日线中枢且收盘价在 ZG 的 95% 以上，但尚未形成三买触发。",
            "- 左侧观察：中枢未明确，或价格仍在中枢中下部/下方，仅作资金异动的跟踪池。",
            "- 量能：突破日成交量 / 前 5 日均量，≥1.2 放量、≥0.8 平量、其余缩量。",
            "- 顶背驰：离开段出现的顶背驰提示，是风险标签，不直接否决信号。",
            "",
            "## 过滤条件",
            "",
            "- 月线：MA5 > MA10 > MA20，收盘在 MA20 上方，MACD DIF/DEA 均在零轴上方。",
            "- 周线：收盘价不高于 10 周均线的 1.3 倍（非暴涨）。",
            "- 资金：近 10 日大单净占比温和（0~15%）、无单日暴入（<30%）、加权均值转正，",
            "  且近 3 日不能出现两次以上净流出超 5%、三日合计必须为正。",
            "- 位置：现价距 120 日最高点回撤不超过 30%，剔除下跌中继。",
            "- 量能：25 日价格横盘（涨幅 <8%）且 OBV 或 A/D 归一化斜率 > 0.05。",
            "- 换手：20 日中位数在 2%~8%，且最近 5 日均值 > 1%。",
            "- 资金口径使用 `moneyflow`（大单+超大单）换算，替代无权限的 `moneyflow_dc`。",
            "",
            "评分只是排序参考，不构成投资建议。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
