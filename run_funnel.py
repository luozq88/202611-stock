"""分层漏斗选股入口。

流程：
1. 全市场截面粗筛：大单净流入 + 换手率（moneyflow / daily_basic）；
2. 月线：MA5>MA10>MA20、收盘在 MA20 上方、月线 MACD 零轴上方；
3. 周线：收盘不高于 10 周均线的 1.3 倍（非暴涨）；
4. 日线：OBV / A-D 价横量升；
5. 缠论：日线中枢 + 三买结构；
6. 输出三买信号 / 右侧观察 / 左侧观察三层结果。

用法：
    export TUSHARE_TOKEN=xxxx
    python run_funnel.py --end 20261110
    python run_funnel.py --sample 800 --seed 42
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from chan.config import ChanConfig
from chan.data import TushareData, load_universe
from chan.funnel import screen_funnel, rows_to_dataframe
from chan.report import write_csv, write_funnel_markdown

logger = logging.getLogger("funnel")


def _date_str(value) -> str:
    return str(value).replace("-", "")[:8]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="分层漏斗 A 股选股（Tushare）")
    parser.add_argument("--token", default=os.getenv("TUSHARE_TOKEN"), help="Tushare token")
    parser.add_argument("--end", help="结束日期 YYYYMMDD，默认最近交易日")
    parser.add_argument(
        "--start",
        default="20160101",
        help="日线起点，默认 20160101（月线 MACD 需要足够长的历史）",
    )
    parser.add_argument("--codes", help="股票代码文件（csv）或逗号分隔代码")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只")
    parser.add_argument("--sample", type=int, default=0, help="全市场随机抽样 N 只")
    parser.add_argument("--seed", type=int, default=42, help="随机抽样种子")
    parser.add_argument("--cache-dir", default="data/cache", help="本地缓存目录")
    parser.add_argument("--output", default="output", help="结果目录")
    parser.add_argument("--refresh", action="store_true", help="强制刷新本地缓存")
    parser.add_argument("--sleep", type=float, default=0.2, help="每次请求后的等待秒数")
    parser.add_argument("--panel-days", type=int, default=30, help="截面回看交易日数")

    # 月线 / 周线
    parser.add_argument("--no-monthly-filter", action="store_true", help="关闭月线过滤")
    parser.add_argument(
        "--no-macd-zero",
        action="store_true",
        help="月线不要求 MACD DIF/DEA 在零轴上方",
    )
    parser.add_argument("--monthly-ma-min-months", type=int, default=24, help="月线最少月数")
    parser.add_argument("--monthly-macd-months", type=int, default=36, help="月线 MACD 最少月数")
    parser.add_argument("--no-surge-filter", action="store_true", help="关闭周线非暴涨过滤")
    parser.add_argument("--surge-ratio", type=float, default=1.3, help="周收盘/周MA10 上限")

    # 资金 / 换手
    parser.add_argument("--no-fund-filter", action="store_true", help="关闭大单净流入过滤")
    parser.add_argument("--mf-days", type=int, default=10, help="资金流回看交易日数")
    parser.add_argument("--mf-moderate", type=float, default=15.0, help="温和净流入上限（百分比）")
    parser.add_argument("--mf-spike", type=float, default=30.0, help="单日净流入绝对值上限（百分比）")
    parser.add_argument("--mf-continuity", type=float, default=0.6, help="温和区间天数占比")
    parser.add_argument("--mf-veto-days", type=int, default=3, help="近期资金否决窗口")
    parser.add_argument("--mf-veto-threshold", type=float, default=-5.0, help="否决窗口单日净流出阈值")
    parser.add_argument("--mf-veto-hits", type=int, default=2, help="否决窗口异常次数上限")
    parser.add_argument("--mf-decay", type=float, default=0.5, help="资金加权均值衰减系数")
    parser.add_argument("--no-position-filter", action="store_true", help="关闭位置回撤过滤")
    parser.add_argument("--position-dd", type=float, default=0.30, help="距区间高点最大回撤，0.3 表示三成")
    parser.add_argument("--position-lookback", type=int, default=120, help="位置回撤回看天数")
    parser.add_argument("--no-turnover-filter", action="store_true", help="关闭换手率过滤")
    parser.add_argument("--turnover-min", type=float, default=2.0, help="换手中位数下限（百分比）")
    parser.add_argument("--turnover-max", type=float, default=8.0, help="换手中位数上限（百分比）")

    # OBV
    parser.add_argument("--no-obv-filter", action="store_true", help="关闭 OBV/A-D 过滤")
    parser.add_argument("--obv-lookback", type=int, default=25, help="OBV 回看天数")
    parser.add_argument("--obv-flat", type=float, default=0.08, help="横盘涨幅上限")
    parser.add_argument("--obv-slope", type=float, default=0.05, help="归一化斜率阈值")

    # 股票池 / 结构
    parser.add_argument("--min-list-months", type=int, default=60, help="上市最少月数")
    parser.add_argument("--min-price", type=float, default=2.0, help="最低股价")
    parser.add_argument("--min-pivot-width", type=float, default=0.02, help="中枢最小宽度")
    parser.add_argument("--min-pivot-bars", type=int, default=8, help="中枢最少K线数")
    parser.add_argument("--breakout-pct", type=float, default=0.01, help="突破 ZG 最小幅度")
    parser.add_argument("--min-strength", type=float, default=0.5, help="突破力度下限")
    parser.add_argument("--max-days", type=int, default=30, help="突破高点距今天数上限")
    parser.add_argument(
        "--min-stage",
        choices=["breakout_only", "pullback_above_zg", "daily_pullback", "second_launch"],
        default="breakout_only",
        help="三买信号的最低回抽阶段",
    )

    parser.add_argument("--confirm-30m", action="store_true", help="对三买信号做30分钟确认")
    parser.add_argument("--confirm-top", type=int, default=20, help="只确认评分前 N 只")
    parser.add_argument("--top", type=int, default=40, help="Markdown 展示前 N 只")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        data = TushareData(
            args.token,
            cache_dir=args.cache_dir,
            sleep=args.sleep,
            refresh=args.refresh,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("初始化 Tushare 失败：%s", exc)
        return 2

    try:
        end_date = _date_str(args.end or data.latest_trade_date())
        end_dt = datetime.strptime(end_date, "%Y%m%d")
        start_date = _date_str(args.start)
    except Exception as exc:  # noqa: BLE001
        logger.error("日期处理失败：%s", exc)
        return 2

    cfg = ChanConfig(
        require_monthly_bull=not args.no_monthly_filter,
        monthly_ma_min_months=args.monthly_ma_min_months,
        monthly_require_macd_zero=not args.no_macd_zero,
        monthly_min_months_for_macd=args.monthly_macd_months,
        require_not_surged=not args.no_surge_filter,
        max_weekly_deviation=args.surge_ratio,
        require_fund_pass=not args.no_fund_filter,
        mf_recent_days=args.mf_days,
        mf_moderate_max=args.mf_moderate,
        mf_spike_max=args.mf_spike,
        mf_continuity=args.mf_continuity,
        mf_veto_days=args.mf_veto_days,
        mf_veto_threshold=args.mf_veto_threshold,
        mf_veto_hits=args.mf_veto_hits,
        mf_decay=args.mf_decay,
        require_position_filter=not args.no_position_filter,
        position_max_drawdown=-abs(args.position_dd),
        position_lookback=args.position_lookback,
        require_turnover_pass=not args.no_turnover_filter,
        turnover_min=args.turnover_min,
        turnover_max=args.turnover_max,
        require_obv_pass=not args.no_obv_filter,
        obv_lookback=args.obv_lookback,
        obv_price_flat_max=args.obv_flat,
        obv_slope_min=args.obv_slope,
        min_list_months=args.min_list_months,
        min_price=args.min_price,
        min_pivot_width_pct=args.min_pivot_width,
        min_pivot_bars=args.min_pivot_bars,
        breakout_min_pct=args.breakout_pct,
        min_strength_ratio=args.min_strength,
        max_days_since_breakout=args.max_days,
    )

    try:
        universe = load_universe(data, args.codes)
    except Exception as exc:  # noqa: BLE001
        logger.error("获取股票列表失败：%s", exc)
        return 2

    if "list_date" in universe.columns:
        listed = pd.to_datetime(universe["list_date"], format="%Y%m%d", errors="coerce")
        cutoff = end_dt - timedelta(days=int(args.min_list_months * 30.4))
        universe = universe[listed <= cutoff].reset_index(drop=True)

    if args.sample > 0:
        n = min(args.sample, len(universe))
        universe = universe.sample(n=n, random_state=args.seed).reset_index(drop=True)
        logger.info("随机抽样：%s 只（seed=%s）", n, args.seed)
    if args.limit > 0:
        universe = universe.head(args.limit).reset_index(drop=True)

    logger.info("筛选区间：%s ~ %s，股票池 %s 只", start_date, end_date, len(universe))
    if universe.empty:
        logger.warning("股票池为空")
        return 0

    try:
        rows = screen_funnel(
            data,
            universe,
            cfg,
            end_date=end_date,
            start_date=start_date,
            panel_days=args.panel_days,
            min_stage=args.min_stage,
            confirm_30m=args.confirm_30m,
            confirm_top=args.confirm_top,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("漏斗执行失败：%s", exc)
        return 2

    df = rows_to_dataframe(rows)
    stamp = datetime.now().strftime("%H%M%S")
    out_dir = Path(args.output)
    csv_path = out_dir / f"funnel_{end_date}_{stamp}.csv"
    md_path = out_dir / f"funnel_{end_date}_{stamp}.md"
    write_csv(df, csv_path)
    write_funnel_markdown(df, md_path, top_n=args.top)

    counts = df["pool"].value_counts().to_dict() if not df.empty else {}
    logger.info(
        "漏斗完成：三买信号 %s，右侧观察 %s，左侧观察 %s",
        counts.get("signal", 0),
        counts.get("buy_watch", 0),
        counts.get("left_watch", 0),
    )
    logger.info("  CSV: %s", csv_path)
    logger.info("  Markdown: %s", md_path)

    if df.empty:
        return 0

    show = df.head(args.top)[
        [
            "ts_code",
            "name",
            "pool",
            "close",
            "monthly_ma5",
            "monthly_ma20",
            "weekly_deviation",
            "mf_mean_rate",
            "turnover_median",
            "pivot_zg",
            "pullback_status",
            "score",
        ]
    ].copy()
    show["pool"] = show["pool"].map(
        {"signal": "三买", "buy_watch": "右侧", "left_watch": "左侧"}
    )
    for col in ("close", "monthly_ma5", "monthly_ma20", "pivot_zg"):
        show[col] = show[col].map(lambda x: "-" if pd.isna(x) else f"{x:.2f}")
    show["weekly_deviation"] = show["weekly_deviation"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.2f}"
    )
    show["mf_mean_rate"] = show["mf_mean_rate"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.2f}%"
    )
    show["turnover_median"] = show["turnover_median"].map(
        lambda x: "-" if pd.isna(x) else f"{x:.2f}%"
    )
    show["score"] = show["score"].map(lambda x: f"{x:.1f}")
    print(show.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
