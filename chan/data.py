"""Tushare 数据访问与本地缓存。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _norm_date(value) -> str:
    """把 20250101 / 2025-01-01 / Timestamp 统一成 YYYYMMDD。"""
    text = str(value).replace("-", "")
    return text[:8]


def bars_from_frame(df: pd.DataFrame) -> list:
    """把 Tushare 日线 DataFrame 转成内部 Bar 列表。"""
    from .structure import Bar

    bars = []
    for record in df.to_dict("records"):
        def _num(value) -> float:
            try:
                return 0.0 if pd.isna(value) else float(value)
            except (TypeError, ValueError):
                return 0.0

        bars.append(
            Bar(
                date=_norm_date(record["trade_date"]),
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


class TushareData:
    def __init__(
        self,
        token: Optional[str],
        cache_dir: str = "data/cache",
        sleep: float = 0.2,
        refresh: bool = False,
    ) -> None:
        if not token:
            raise ValueError(
                "缺少 Tushare token：请设置环境变量 TUSHARE_TOKEN，或使用 --token 传入。"
            )
        try:
            import tushare as ts
        except ImportError as exc:
            raise RuntimeError(
                "未安装 tushare，请先执行: pip install -r requirements.txt"
            ) from exc

        self.ts = ts
        self.pro = ts.pro_api(token)
        self.cache_dir = Path(cache_dir)
        self.daily_dir = self.cache_dir / "daily"
        self.index_dir = self.cache_dir / "index"
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.sleep = sleep
        self.refresh = refresh

    def _request(self, func, **kwargs):
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                df = func(**kwargs)
                if self.sleep > 0:
                    time.sleep(self.sleep)
                return df
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                wait = 1.5 * (attempt + 1)
                logger.warning("Tushare 请求失败（第 %s 次）：%s，%.1fs 后重试", attempt + 1, exc, wait)
                time.sleep(wait)
        assert last_exc is not None
        raise last_exc

    def latest_trade_date(self) -> str:
        today = datetime.now()
        start = (today - timedelta(days=30)).strftime("%Y%m%d")
        cal = self._request(
            self.pro.trade_cal,
            exchange="SSE",
            is_open="1",
            start_date=start,
            end_date=today.strftime("%Y%m%d"),
        )
        if cal is None or cal.empty:
            return today.strftime("%Y%m%d")
        return str(cal["cal_date"].max())

    def get_stock_list(self) -> pd.DataFrame:
        df = self._request(
            self.pro.stock_basic,
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,market,list_date",
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["ts_code", "name", "industry"])

        df = df[~df["name"].str.contains("ST", na=False)]
        df = df[~df["ts_code"].str.endswith(".BJ")]
        # 排除B股
        df = df[~df["ts_code"].str.match(r"^(200|900)")]
        return df.reset_index(drop=True)

    def get_trade_dates(self, n: int, end_date: str) -> list[str]:
        """取 end_date 之前最近的 n 个交易日（含 end_date）。"""
        end_dt = datetime.strptime(end_date, "%Y%m%d")
        start_dt = end_dt - timedelta(days=n * 2 + 40)
        cal = self._request(
            self.pro.trade_cal,
            exchange="SSE",
            is_open="1",
            start_date=start_dt.strftime("%Y%m%d"),
            end_date=end_date,
        )
        if cal is None or cal.empty:
            return []
        dates = sorted(cal["cal_date"].astype(str).tolist())
        return dates[-n:]

    def get_panel(
        self,
        api: str,
        trade_date: str,
        fields: Optional[str] = None,
    ) -> pd.DataFrame:
        """拉取某个交易日的全市场截面数据，并按天缓存。

        `daily` / `daily_basic` / `moneyflow` 都支持 trade_date 全市场查询，
        比逐股拉取省一个数量级的请求次数。
        """
        trade_date = _norm_date(trade_date)
        panel_dir = self.cache_dir / "panel" / api
        path = panel_dir / f"{trade_date}.csv"
        today = datetime.now().strftime("%Y%m%d")

        # 当天数据可能还没收盘完整，不缓存，直接重取
        if path.exists() and not self.refresh and trade_date < today:
            try:
                df = pd.read_csv(path, dtype={"ts_code": str, "trade_date": str})
                if not df.empty:
                    return df
            except Exception as exc:  # noqa: BLE001
                logger.debug("截面缓存读取失败 %s：%s", path, exc)

        func = getattr(self.pro, api)
        kwargs = {"trade_date": trade_date}
        if fields:
            kwargs["fields"] = fields
        df = self._request(func, **kwargs)
        if df is None:
            df = pd.DataFrame()
        if not df.empty:
            df = df.copy()
            if "trade_date" in df.columns:
                df["trade_date"] = df["trade_date"].astype(str)
            panel_dir.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, index=False, encoding="utf-8-sig")
        return df

    def _cache_path(self, ts_code: str) -> Path:
        return self.daily_dir / f"{ts_code}.csv"

    def _meta_path(self, ts_code: str) -> Path:
        return self.daily_dir / f"{ts_code}.meta.json"

    def _fetch_qfq_daily(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """自行拼接 daily + adj_factor 做前复权。

        不走 ts.pro_bar，避免其内部 fillna(method=...) 在新版 pandas 上不兼容。
        """
        daily = self._request(
            self.pro.daily,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        if daily is None or daily.empty:
            return pd.DataFrame()

        daily = daily.copy()
        daily["trade_date"] = daily["trade_date"].map(_norm_date)
        for col in ("open", "high", "low", "close", "pre_close"):
            if col in daily.columns:
                daily[col] = pd.to_numeric(daily[col], errors="coerce")
        daily = daily.sort_values("trade_date").reset_index(drop=True)

        try:
            adj = self._request(
                self.pro.adj_factor,
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 复权因子获取失败，使用不复权数据：%s", ts_code, exc)
            return daily

        if adj is None or adj.empty:
            return daily

        adj = adj.copy()
        adj["trade_date"] = adj["trade_date"].map(_norm_date)
        adj["adj_factor"] = pd.to_numeric(adj["adj_factor"], errors="coerce")
        adj = adj.sort_values("trade_date").reset_index(drop=True)
        merged = daily.merge(
            adj[["trade_date", "adj_factor"]], on="trade_date", how="left"
        )
        merged["adj_factor"] = merged["adj_factor"].ffill().bfill()
        if merged.empty:
            return merged

        latest = merged["adj_factor"].iloc[-1]
        if pd.notna(latest) and float(latest) != 0:
            for col in ("open", "high", "low", "close", "pre_close"):
                if col in merged.columns:
                    merged[col] = merged[col] * merged["adj_factor"] / float(latest)
        return merged.drop(columns=["adj_factor"], errors="ignore")

    def get_daily(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        refresh: bool = False,
    ) -> pd.DataFrame:
        cache = self._cache_path(ts_code)
        meta_path = self._meta_path(ts_code)
        today = datetime.now().strftime("%Y%m%d")

        if cache.exists() and not (refresh or self.refresh):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
            if (
                meta.get("end", "") >= end_date
                and meta.get("start", "") <= start_date
                and meta.get("fetched_on") == today
            ):
                try:
                    df = pd.read_csv(cache, dtype={"trade_date": str})
                except Exception:  # noqa: BLE001
                    df = pd.DataFrame()
                if not df.empty:
                    df["trade_date"] = df["trade_date"].map(_norm_date)
                    mask = (df["trade_date"] >= start_date) & (
                        df["trade_date"] <= end_date
                    )
                    return df.loc[mask].reset_index(drop=True)

        df = self._fetch_qfq_daily(ts_code, start_date, end_date)

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.copy()
        df["trade_date"] = df["trade_date"].map(_norm_date)
        df = df.sort_values("trade_date").reset_index(drop=True)
        for col in ("open", "high", "low", "close", "vol", "amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

        df.to_csv(cache, index=False)
        meta_path.write_text(
            json.dumps(
                {
                    "start": start_date,
                    "end": end_date,
                    "fetched_on": today,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return df

    def get_index_daily(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        cache = self.index_dir / f"{ts_code}.csv"
        if cache.exists() and not self.refresh:
            try:
                df = pd.read_csv(cache, dtype={"trade_date": str})
                if not df.empty:
                    df["trade_date"] = df["trade_date"].map(_norm_date)
                if (
                    not df.empty
                    and df["trade_date"].min() <= start_date
                    and df["trade_date"].max() >= end_date
                ):
                    mask = (df["trade_date"] >= start_date) & (
                        df["trade_date"] <= end_date
                    )
                    return df.loc[mask].reset_index(drop=True)
            except Exception as exc:  # noqa: BLE001
                logger.debug("指数缓存读取失败：%s", exc)

        df = self._request(
            self.pro.index_daily,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        if df is None or df.empty:
            return pd.DataFrame()

        df["trade_date"] = df["trade_date"].map(_norm_date)
        df = df.sort_values("trade_date").reset_index(drop=True)
        df.to_csv(cache, index=False)
        return df

    def get_minutes(
        self,
        ts_code: str,
        freq: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """获取分钟线。

        Tushare 分钟行情需要相应积分权限；不同账号可用范围不同。
        """
        start_date = _norm_date(start_date)
        end_date = _norm_date(end_date)
        start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]} 09:00:00"
        end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]} 15:30:00"
        df = None
        try:
            df = self._request(
                self.pro.stk_mins,
                ts_code=ts_code,
                freq=freq,
                start_date=start,
                end_date=end,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 分钟行情获取失败（%s）：%s", ts_code, freq, exc)
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.sort_values("trade_time").reset_index(drop=True)
        for col in ("open", "high", "low", "close", "vol", "amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def load_universe(data: TushareData, codes_arg: Optional[str]) -> pd.DataFrame:
    """构建股票池：全市场，或 --codes 指定的 CSV/逗号分隔代码。"""
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

