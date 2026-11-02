"""缠论基础结构：K线包含处理、分型、笔、中枢。

这里采用工程上常用的简化实现：
1. 先做包含关系合并；
2. 在合并K线上找顶/底分型；
3. 按新笔标准（两个分型之间至少间隔 min_gap 根合并K线）连笔；
4. 连续三笔重叠构成中枢，中枢延伸/假离开都做了处理。

它不追求教科书式的完全递归级别划分，但能稳定复现“突破中枢后回抽不碰 ZG”
这一核心三买结构，适合批量选股。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass(slots=True)
class Bar:
    """原始K线。"""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    idx: int = -1


@dataclass(slots=True)
class MergedBar:
    """包含处理后的合并K线，保留原始极值位置。"""

    start_idx: int
    end_idx: int
    start_date: str
    end_date: str
    high: float
    low: float
    high_idx: int
    high_date: str
    low_idx: int
    low_date: str


@dataclass(slots=True)
class Fractal:
    """顶/底分型。"""

    kind: str  # "top" | "bottom"
    m_idx: int  # 在合并K线中的下标
    idx: int  # 原始K线下标（顶对应 high_idx，底对应 low_idx）
    date: str
    price: float


@dataclass(slots=True)
class Stroke:
    """笔。"""

    start: Fractal
    end: Fractal
    direction: str  # "up" | "down"
    start_idx: int
    end_idx: int
    high: float
    low: float

    @property
    def amplitude(self) -> float:
        return self.high - self.low

    @property
    def bar_count(self) -> int:
        return self.end_idx - self.start_idx + 1


@dataclass(slots=True)
class Pivot:
    """中枢。"""

    start_stroke: int
    end_stroke: int
    leave_stroke: Optional[int]
    zg: float
    zd: float
    gg: float
    dd: float
    start_idx: int
    end_idx: int
    start_date: str
    end_date: str
    bar_count: int

    @property
    def width_pct(self) -> float:
        return (self.zg - self.zd) / self.zd if self.zd else 0.0


def merge_bars(bars: Sequence[Bar]) -> List[MergedBar]:
    """处理K线包含关系。"""
    merged: List[MergedBar] = []

    for i, bar in enumerate(bars):
        if not merged:
            merged.append(
                MergedBar(
                    start_idx=i,
                    end_idx=i,
                    start_date=bar.date,
                    end_date=bar.date,
                    high=bar.high,
                    low=bar.low,
                    high_idx=i,
                    high_date=bar.date,
                    low_idx=i,
                    low_date=bar.date,
                )
            )
            continue

        last = merged[-1]
        contained = bar.high <= last.high and bar.low >= last.low
        containing = bar.high >= last.high and bar.low <= last.low
        if not (contained or containing):
            merged.append(
                MergedBar(
                    start_idx=i,
                    end_idx=i,
                    start_date=bar.date,
                    end_date=bar.date,
                    high=bar.high,
                    low=bar.low,
                    high_idx=i,
                    high_date=bar.date,
                    low_idx=i,
                    low_date=bar.date,
                )
            )
            continue

        # 判断当前合并方向：向上取高高，向下取低低
        if len(merged) >= 2:
            prev = merged[-2]
            up = last.high > prev.high
        else:
            up = bar.close >= bars[i - 1].close

        if up:
            if bar.high >= last.high:
                last.high = bar.high
                last.high_idx = i
                last.high_date = bar.date
            if bar.low >= last.low:
                last.low = bar.low
                last.low_idx = i
                last.low_date = bar.date
        else:
            if bar.high <= last.high:
                last.high = bar.high
                last.high_idx = i
                last.high_date = bar.date
            if bar.low <= last.low:
                last.low = bar.low
                last.low_idx = i
                last.low_date = bar.date

        last.end_idx = i
        last.end_date = bar.date

    return merged


def find_fractals(merged: Sequence[MergedBar]) -> List[Fractal]:
    """在合并K线上识别顶底分型。"""
    fractals: List[Fractal] = []

    for i in range(1, len(merged) - 1):
        prev, cur, nxt = merged[i - 1], merged[i], merged[i + 1]

        is_top = (
            cur.high > prev.high
            and cur.high > nxt.high
            and cur.low > prev.low
            and cur.low > nxt.low
        )
        is_bottom = (
            cur.low < prev.low
            and cur.low < nxt.low
            and cur.high < prev.high
            and cur.high < nxt.high
        )

        if is_top:
            fractals.append(
                Fractal(
                    kind="top",
                    m_idx=i,
                    idx=cur.high_idx,
                    date=cur.high_date,
                    price=cur.high,
                )
            )
        elif is_bottom:
            fractals.append(
                Fractal(
                    kind="bottom",
                    m_idx=i,
                    idx=cur.low_idx,
                    date=cur.low_date,
                    price=cur.low,
                )
            )

    return fractals


def build_strokes(
    fractals: Sequence[Fractal], min_gap: int = 4
) -> List[Stroke]:
    """由分型连接成笔。

    min_gap 为两个分型中心之间至少间隔的合并K线数。默认 4，即两个分型
    之间至少有一根独立K线（新笔标准常用 4 或 5）。
    """
    if len(fractals) < 2:
        return []

    endpoints: List[Fractal] = []
    for fx in fractals:
        if not endpoints:
            endpoints.append(fx)
            continue

        last = endpoints[-1]
        if fx.kind == last.kind:
            # 同类分型取更极端的一个
            if (fx.kind == "top" and fx.price > last.price) or (
                fx.kind == "bottom" and fx.price < last.price
            ):
                endpoints[-1] = fx
            continue

        # 异类分型：间隔不足则忽略
        if fx.m_idx - last.m_idx < min_gap:
            continue

        # 价格方向必须成立：顶必须高于底
        if last.kind == "top" and fx.price >= last.price:
            continue
        if last.kind == "bottom" and fx.price <= last.price:
            continue

        endpoints.append(fx)

    strokes: List[Stroke] = []
    for start, end in zip(endpoints, endpoints[1:]):
        direction = "up" if start.kind == "bottom" else "down"
        high = max(start.price, end.price)
        low = min(start.price, end.price)
        strokes.append(
            Stroke(
                start=start,
                end=end,
                direction=direction,
                start_idx=start.idx,
                end_idx=end.idx,
                high=high,
                low=low,
            )
        )

    return strokes


def find_pivots(
    strokes: Sequence[Stroke],
    min_width_pct: float = 0.02,
    min_bars: int = 8,
) -> List[Pivot]:
    """识别中枢。

    连续三笔重叠构成中枢，ZG=三笔高点最小值，ZD=三笔低点最大值。
    后续笔如果收在中枢区间内，视为中枢延伸；如果离开后下一笔又回到
    中枢内，视为假离开，继续延伸。真正离开的那一笔记录为 leave_stroke。
    """
    pivots: List[Pivot] = []
    n = len(strokes)
    i = 0

    while i + 2 < n:
        s1, s2, s3 = strokes[i], strokes[i + 1], strokes[i + 2]
        zg = min(s1.high, s2.high, s3.high)
        zd = max(s1.low, s2.low, s3.low)

        if zg <= zd:
            i += 1
            continue

        gg = max(s1.high, s2.high, s3.high)
        dd = min(s1.low, s2.low, s3.low)
        last_included = i + 2
        j = i + 3

        while j < n:
            stroke = strokes[j]
            if zd <= stroke.end.price <= zg:
                gg = max(gg, stroke.high)
                dd = min(dd, stroke.low)
                last_included = j
                j += 1
                continue

            # 假离开：下一笔又回到中枢区间
            if j + 1 < n and zd <= strokes[j + 1].end.price <= zg:
                gg = max(gg, stroke.high, strokes[j + 1].high)
                dd = min(dd, stroke.low, strokes[j + 1].low)
                last_included = j + 1
                j += 2
                continue

            break

        leave = j if j < n else None
        start_idx = s1.start_idx
        end_idx = strokes[last_included].end_idx
        pivot = Pivot(
            start_stroke=i,
            end_stroke=last_included,
            leave_stroke=leave,
            zg=zg,
            zd=zd,
            gg=gg,
            dd=dd,
            start_idx=start_idx,
            end_idx=end_idx,
            start_date=s1.start.date,
            end_date=strokes[last_included].end.date,
            bar_count=end_idx - start_idx + 1,
        )

        if pivot.width_pct >= min_width_pct and pivot.bar_count >= min_bars:
            pivots.append(pivot)

        i = max(j, i + 1)

    return pivots
