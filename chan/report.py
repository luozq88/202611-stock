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
