"""选股参数。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ChanConfig:
    """缠论三买选股参数。

    默认值对应讨论中的框架：
    日线一笔向上离开中枢 -> 回抽不碰中枢上沿 ZG -> 三买候选。
    """

    # 月线过滤
    require_monthly_bull: bool = True
    monthly_ma_min_months: int = 20
    monthly_include_current: bool = True

    # 结构
    min_stroke_gap: int = 4          # 一笔至少间隔的合并K线数（新笔标准）
    min_pivot_width_pct: float = 0.02  # 中枢最小宽度
    min_pivot_bars: int = 8          # 中枢最少持续K线数
    min_bars: int = 120              # 最少日线数据量

    # 突破
    breakout_min_pct: float = 0.01   # 离开段至少高于 ZG 的幅度
    min_strength_ratio: float = 0.5  # 离开段力度 / 前一下跌段力度
    max_days_since_breakout: int = 30  # 突破高点距今天数

    # 回抽
    pullback_tolerance: float = 0.0  # 回抽低点相对 ZG 的容差，0 表示严格不碰
    min_daily_pullback_bars: int = 2  # 日线回抽最少K线数（强势股常常只有1-2根）
    min_pullback_bars: int = 3       # 30分钟回抽最少K线数

    # 背驰
    reject_top_divergence: bool = True
    require_bottom_divergence: bool = False

    # 其他过滤
    min_price: float = 2.0
    min_avg_amount: float = 0.0      # 近20日平均成交额，单位千元；0 表示不过滤
    use_ma_filter: bool = False
    ma_short: int = 20
    ma_long: int = 60

    # 风控
    stop_buffer_pct: float = 0.01    # 中枢上沿下方 1% 作为硬止损
