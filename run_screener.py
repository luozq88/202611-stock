"""缠论三买选股入口。

用法示例：
    export TUSHARE_TOKEN=xxxx
    python run_screener.py --start 20250101 --end 20251101

先小范围验证：
    python run_screener.py --limit 100 --confirm-30m
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
from chan.confirm import confirm_minute_pullback
from chan.data import TushareData
from chan.report import results_to_dataframe, write_csv, write_markdown
from chan.screener import ScreenResult, analyze_stock
from chan.structure import Bar

logger = logging.getLogger("screener")

STAGE_ORDER = {
    "breakout_only": 0,
    "pullback_above_zg": 1,
    "daily_pullback": 2,
    "second_launch": 3,
}


def _num(value) -> float:
    try:
        if pd.isna(value):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _date_str(value) -> str:
    return str(value).replace("-", "")[:8]


def df_to_bars(df: pd.DataFrame) -> List[Bar]:
    bars: List[Bar] = []
    for record in df.to_dict("records"):
        bars.append(
            Bar(
                date=_date_str(record["trade_date"]),
                open=float(record["open"]),
                high=float(record["high"]),
                low=float(record["low"]),
                close=float(record["close"]),
                volume=_num(record.get("vol")),
                amount=_num(record.get("amount")),
                idx=len(bars),
            )
        )
    return bars


def load_universe(data: TushareData, codes_arg: Optional[str]) -> pd.DataFrame:
    stock_list = data.get_stock_list()

    if not codes_arg:
        return stock_list

    path = Path(codes_arg)
    if path.exists():
        codes_df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        if "ts_code" in codes_df.columns:
            codes = codes_df["ts_code"].dropna().astype(str).tolist()
        else:
            codes = codes_df.iloc[:, 0].dropna().astype(str).tolist()
    else:
        codes = [item.strip() for item in codes_arg.split(",") if item.strip()]

    codes_df = pd.DataFrame({"ts_code": codes})
    merged = codes_df.merge(
        stock_list[["ts_code", "name", "industry"]], on="ts_code", how="left"
    )
    merged["name"] = merged["name"].fillna(merged["ts_code"])
    merged["industry"] = merged["industry"].fillna("")
    return merged


def check_market(data: TushareData, start_date: str, end_date: str) -> Optional[bool]:
    try:
        idx = data.get_index_daily("000001.SH", start_date, end_date)
        if idx is None or len(idx) < 20:
            return None
        close = idx.sort_values("trade_date")["close"].astype(float)
        ma20 = float(close.tail(20).mean())
        return bool(close.iloc[-1] > ma20)
    except Exception as exc:  # noqa: BLE001
        logger.warning("大盘环境判断失败：%s", exc)
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="基于缠论三买结构的 A 股选股（Tushare）"
    )
    parser.add_argument("--token", default=os.getenv("TUSHARE_TOKEN"), help="Tushare token")
    parser.add_argument("--start", help="开始日期 YYYYMMDD")
    parser.add_argument("--end", help="结束日期 YYYYMMDD，默认最近交易日")
    parser.add_argument("--lookback", type=int, default=500, help="回看自然日数")
    parser.add_argument("--codes", help="股票代码文件（csv）或逗号分隔代码")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只，0 表示全部")
    parser.add_argument("--sample", type=int, default=0, help="从全市场随机抽样 N 只，0 表示不抽样")
    parser.add_argument("--seed", type=int, default=42, help="随机抽样种子")
    parser.add_argument("--cache-dir", default="data/cache", help="本地缓存目录")
    parser.add_argument("--output", default="output", help="结果目录")
    parser.add_argument("--refresh", action="store_true", help="强制刷新本地缓存")
    parser.add_argument("--sleep", type=float, default=0.2, help="每次 Tushare 请求后的等待秒数")

    parser.add_argument("--breakout-pct", type=float, default=0.01, help="突破 ZG 的最小幅度")
    parser.add_argument("--min-strength", type=float, default=0.5, help="离开段/前一下跌段力度比")
    parser.add_argument("--max-days", type=int, default=30, help="突破高点距今天数上限")
    parser.add_argument("--min-pivot-width", type=float, default=0.02, help="中枢最小宽度")
    parser.add_argument("--min-pivot-bars", type=int, default=8, help="中枢最少K线数")
    parser.add_argument("--min-daily-pullback-bars", type=int, default=2, help="日线回抽最少K线数")
    parser.add_argument("--min-pullback-bars", type=int, default=3, help="30分钟回抽最少K线数")
    parser.add_argument("--min-price", type=float, default=2.0, help="最低股价")
    parser.add_argument("--min-amount", type=float, default=0.0, help="近20日平均成交额（千元）")
    parser.add_argument("--ma-filter", action="store_true", help="要求收盘价 > MA20 > MA60")
    parser.add_argument(
        "--allow-top-divergence",
        action="store_true",
        help="允许突破段出现顶背驰（默认剔除）",
    )
    parser.add_argument(
        "--min-stage",
        choices=list(STAGE_ORDER),
        default="breakout_only",
        help="最低回抽阶段",
    )

    parser.add_argument("--market-filter", action="store_true", help="计算并输出大盘环境")
    parser.add_argument("--confirm-30m", action="store_true", help="用30分钟线确认回抽")
    parser.add_argument("--require-sub", action="store_true", help="只保留30分钟确认通过的标的")
    parser.add_argument("--confirm-top", type=int, default=30, help="只对评分前 N 只做分钟确认")
    parser.add_argument("--top", type=int, default=30, help="Markdown 展示前 N 只")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.require_sub:
        args.confirm_30m = True

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
    except Exception as exc:  # noqa: BLE001
        logger.error("获取交易日历失败：%s", exc)
        return 2

    try:
        end_dt = datetime.strptime(end_date, "%Y%m%d")
    except ValueError:
        logger.error("结束日期格式错误：%s，请使用 YYYYMMDD", end_date)
        return 2
    if args.start:
        start_date = _date_str(args.start)
    else:
        start_date = (end_dt - timedelta(days=args.lookback)).strftime("%Y%m%d")
    stale_before = (end_dt - timedelta(days=15)).strftime("%Y%m%d")

    logger.info("筛选区间：%s ~ %s", start_date, end_date)

    market_ok = check_market(data, start_date, end_date) if args.market_filter else None
    if args.market_filter:
        if market_ok is True:
            logger.info("大盘环境：上证指数在 MA20 上方")
        elif market_ok is False:
            logger.warning("大盘环境：上证指数在 MA20 下方，逆风环境请降低仓位")
        else:
            logger.warning("大盘环境：数据不足，跳过判断")

    try:
        universe = load_universe(data, args.codes)
    except Exception as exc:  # noqa: BLE001
        logger.error("获取股票列表失败：%s", exc)
        return 2
    if args.sample > 0:
        n = min(args.sample, len(universe))
        universe = universe.sample(n=n, random_state=args.seed).reset_index(drop=True)
        logger.info("随机抽样：%s 只（seed=%s）", n, args.seed)
    if args.limit > 0:
        universe = universe.head(args.limit)
    total = len(universe)
    logger.info("待处理股票：%s 只", total)

    cfg = ChanConfig(
        breakout_min_pct=args.breakout_pct,
        min_strength_ratio=args.min_strength,
        max_days_since_breakout=args.max_days,
        min_pivot_width_pct=args.min_pivot_width,
        min_pivot_bars=args.min_pivot_bars,
        min_daily_pullback_bars=args.min_daily_pullback_bars,
        min_pullback_bars=args.min_pullback_bars,
        min_price=args.min_price,
        min_avg_amount=args.min_amount,
        use_ma_filter=args.ma_filter,
        reject_top_divergence=not args.allow_top_divergence,
    )

    results: List[ScreenResult] = []
    errors = 0

    for order, row in enumerate(universe.itertuples(index=False), start=1):
        ts_code = str(row.ts_code)
        name_value = getattr(row, "name", ts_code)
        name = ts_code if pd.isna(name_value) else str(name_value)
        industry_value = getattr(row, "industry", "")
        industry = "" if pd.isna(industry_value) else str(industry_value)

        try:
            df = data.get_daily(ts_code, start_date, end_date)
            if df is None or df.empty:
                continue
            bars = df_to_bars(df)
            if bars[-1].date < stale_before:
                # 长期停牌或数据未更新
                continue
            result = analyze_stock(
                ts_code=ts_code,
                name=name,
                industry=industry,
                bars=bars,
                cfg=cfg,
                market_ok=market_ok,
            )
            if result is not None:
                results.append(result)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.debug("%s 分析失败：%s", ts_code, exc)
            if errors <= 5:
                logger.warning("%s 分析失败：%s", ts_code, exc)

        if order % 100 == 0 or order == total:
            logger.info(
                "进度 %s/%s，候选 %s，错误 %s", order, total, len(results), errors
            )

    # 阶段过滤
    min_rank = STAGE_ORDER[args.min_stage]
    results = [
        item for item in results if STAGE_ORDER.get(item.pullback_status, 0) >= min_rank
    ]
    results.sort(key=lambda item: item.score, reverse=True)

    # 次级别确认
    if args.confirm_30m and results:
        count = min(args.confirm_top, len(results))
        logger.info("开始30分钟回抽确认：前 %s 只", count)
        for item in results[:count]:
            try:
                minute_df = data.get_minutes(
                    item.ts_code, "30min", item.breakout_date, end_date
                )
                ok, note, _ = confirm_minute_pullback(
                    minute_df,
                    item.pivot_zg,
                    item.breakout_date,
                    min_bars=cfg.min_pullback_bars,
                    breakout_high=item.breakout_high,
                )
                item.sub_ok = ok
                item.sub_note = note
                if ok:
                    item.score = min(100.0, item.score + 12.0)
            except Exception as exc:  # noqa: BLE001
                item.sub_ok = None
                item.sub_note = f"分钟确认失败：{exc}"
                logger.debug("%s 分钟确认失败：%s", item.ts_code, exc)

    if args.require_sub:
        results = [item for item in results if item.sub_ok is True]

    results.sort(key=lambda item: item.score, reverse=True)

    df = results_to_dataframe(results)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = Path(args.output)
    csv_path = out_dir / f"candidates_{end_date}_{stamp}.csv"
    md_path = out_dir / f"candidates_{end_date}_{stamp}.md"
    write_csv(df, csv_path)
    write_markdown(df, md_path, top_n=args.top)

    if df.empty:
        logger.info("没有符合条件的标的。结果已写入 %s", csv_path)
        return 0

    logger.info("筛选完成：%s 只候选，结果已写入", len(df))
    logger.info("  CSV: %s", csv_path)
    logger.info("  Markdown: %s", md_path)

    show_cols = [
        "ts_code",
        "name",
        "close",
        "pivot_zg",
        "breakout_date",
        "breakout_pct",
        "strength_ratio",
        "pullback_status",
        "score",
    ]
    show = df[show_cols].head(args.top).copy()
    show["breakout_pct"] = (show["breakout_pct"] * 100).map(lambda x: f"{x:.1f}%")
    show["strength_ratio"] = show["strength_ratio"].map(lambda x: f"{x:.2f}")
    show["score"] = show["score"].map(lambda x: f"{x:.1f}")
    print(show.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
