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
    monthly_ma_min_months: int = 24
    monthly_include_current: bool = True
    monthly_require_macd_zero: bool = True   # 月线 DIF、DEA 均在零轴上方
    monthly_min_months_for_macd: int = 36    # 月线 MACD 至少需要的月数

    # 周线：非暴涨约束
    require_not_surged: bool = True
    max_weekly_deviation: float = 1.3  # 周收盘 / 周MA10 上限
    min_weekly_bars: int = 12

    # 资金流（moneyflow 口径，替代无权限的 moneyflow_dc）
    require_fund_pass: bool = True
    mf_recent_days: int = 10
    mf_moderate_max: float = 15.0   # 温和净流入上限，单位 %（按全市场分布标定）
    mf_spike_max: float = 30.0      # 单日净流入绝对值上限，单位 %
    mf_continuity: float = 0.6      # 处于温和区间的天数占比
    mf_veto_days: int = 3           # 近期否决窗口
    mf_veto_threshold: float = -5.0  # 窗口内单日净流出超过该值视为异常
    mf_veto_hits: int = 2           # 异常次数达到该值即淘汰
    mf_decay: float = 0.5           # 加权均值：最近一天权重增量，0 表示等权

    # 位置：现价距区间高点的回撤，剔除下跌中继
    require_position_filter: bool = True
    position_lookback: int = 120
    position_max_drawdown: float = -0.30  # 现价 / 区间最高 - 1 的下限

    # 换手率
    require_turnover_pass: bool = True
    turnover_recent_days: int = 20
    turnover_min: float = 2.0       # 换手中位数下限，单位 %
    turnover_max: float = 8.0       # 换手中位数上限，单位 %
    turnover_tail_min: float = 1.0  # 最近 5 日均换手下限，单位 %

    # OBV / A-D 价横量升
    require_obv_pass: bool = True
    obv_lookback: int = 25
    obv_price_flat_max: float = 0.08  # 区间涨幅低于 8% 视为横盘
    obv_slope_min: float = 0.05       # 归一化斜率阈值

    # 股票池
    min_list_months: int = 60
    watch_zg_ratio: float = 0.95   # 右侧观察池：收盘价不低于 ZG 的该比例

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
