# 202611-stock

11月的想法：基于缠论“日线中枢突破 + 次级别回抽三买”的 A 股选股脚本，数据来自 Tushare。

仓库提供两个入口：

- `run_funnel.py`：按最新讨论的分层漏斗实现，先截面粗筛再做逐股精筛，**推荐使用**；
- `run_screener.py`：早期的单层扫描版本，只做缠论结构 + 月线过滤，保留用于对比。

## 漏斗选股（run_funnel.py）

### 实现的逻辑

1. **截面粗筛（全市场）**：按交易日批量拉取 `daily` / `daily_basic` / `moneyflow`，一次性完成两件事：
   - 大单资金：近 10 日大单+超大单净流入占比“温和、连续、无单日暴入”（0~15%、绝对峰值 <30%、加权均值转正），且近 3 日不能出现两次以上净流出超 5%、三日合计必须为正；
   - 换手率：20 日中位数 2%~8%，且最近 5 日均值 >1%。
2. **月线（战略层）**：`MA5 > MA10 > MA20`，收盘价在 `MA20` 上方，且月线 `MACD` 的 `DIF`、`DEA` 均在零轴上方；月线由日线前复权收盘合成，默认包含当月未完成K线。
3. **周线（非暴涨）**：周收盘价不高于 10 周均线的 1.3 倍，避免追高。
4. **位置（剔除下跌中继）**：现价距 120 日最高点回撤不超过 30%。
5. **量能（价横量升）**：25 日区间涨幅 <8% 视为横盘，同时 `OBV` 或 `A/D` 的归一化斜率 >0.05。
6. **日线缠论结构**：中枢（`ZG`/`ZD`）+ 向上离开段 + 回抽不碰 `ZG` 的三买识别，沿用 `chan/` 下的实现。
7. **分层输出**：
   - `signal`：三买信号，可直接进入 30 分钟确认；
   - `buy_watch`：已有日线中枢、收盘价在 `ZG` 的 95% 以上，但尚未形成三买触发；
   - `left_watch`：中枢未明确，或价格仍在中枢中下部/下方，只作为资金异动的跟踪池。

### 数据源说明

方案原文使用 `moneyflow_dc`（东财大单净占比）和 `cyq_perf`（筹码集中度），这两个接口分别需要 5000 / 10000 积分。实测当前 token 无权限，因此：

- 资金层改用全市场可用的 `moneyflow`，用 `(buy_lg_amount - sell_lg_amount) / 成交额` 换算成同含义的大单净占比；
- 筹码集中度层整体跳过，不影响漏斗主干。

### 运行

先小范围验证：

```bash
python run_funnel.py --sample 200 --seed 42
```

全市场扫描（建议适当加大 `--sleep` 规避 `adj_factor` 限频）：

```bash
python run_funnel.py --end 20261110 --sleep 0.3
```

只跑指定股票：

```bash
python run_funnel.py --codes 300082.SZ,600036.SH
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--sample` / `--seed` | 随机抽样 N 只，便于快速验证 |
| `--limit` | 只处理股票池前 N 只 |
| `--start` | 日线起点，默认 20160101；月线 MACD 需要长历史，不建议晚于 2018 |
| `--panel-days` | 截面回看交易日数，默认 30 |
| `--no-fund-filter` / `--no-turnover-filter` / `--no-obv-filter` | 分别关闭资金、换手、量能过滤 |
| `--mf-moderate` / `--mf-spike` / `--mf-continuity` | 资金“温和、无暴入、连续”的阈值，默认 15 / 30 / 0.6 |
| `--mf-veto-days` / `--mf-veto-threshold` / `--mf-veto-hits` | 近期资金否决窗口，默认 3 天 / -5% / 2 次 |
| `--mf-decay` | 资金加权均值的衰减系数，默认 0.5 |
| `--no-position-filter` / `--position-dd` / `--position-lookback` | 位置过滤，默认距 120 日高点回撤不超过 30% |
| `--surge-ratio` | 周收盘/周MA10 上限，默认 1.3 |
| `--no-macd-zero` | 月线不要求 MACD 零轴上方 |
| `--obv-lookback` / `--obv-flat` / `--obv-slope` | 量能横盘窗口与斜率阈值 |
| `--min-stage` | 三买信号的最低回抽阶段 |
| `--confirm-30m` / `--confirm-top` | 对三买信号做 30 分钟确认（受 Tushare 限频影响） |

结果写入 `output/funnel_YYYYMMDD_HHMMSS.csv/.md`，按 `signal → buy_watch → left_watch` 排序。

## 单层扫描（run_screener.py，早期版本）

### 实现的逻辑

对应讨论中的方案：

1. **月线趋势过滤（默认开启）**：最近一个月的月线 `MA5 > MA10 > MA20` 多头排列，收盘在 `MA20` 上方，且月线 MACD 的 `DIF`、`DEA` 均在零轴上方。月线由日线前复权收盘价按月合成，默认包含当月未完成K线；可用 `--no-monthly-filter` 关闭。
2. **日线中枢**：连续三笔重叠构成中枢，得到 `ZG`（上沿）、`ZD`（下沿）。
3. **向上离开段**：一笔向上突破 `ZG`，并检查：
   - 突破幅度 >= `--breakout-pct`；
   - 离开段力度 >= 前一下跌段的 `--min-strength`（默认 0.5）；
   - 离开段没有明显顶背驰。
4. **回抽不碰中枢**：突破高点之后的所有日线低点必须严格在 `ZG` 之上，否则视为假突破/中枢延伸。
5. **回抽结构**：
   - `breakout_only`：刚突破，尚未形成明确回抽；
   - `pullback_above_zg`：日线回抽低点在 `ZG` 上方；
   - `daily_pullback`：日线走出完整回抽笔且不碰 `ZG`；
   - `second_launch`：回抽后已经出现向上笔。
6. **次级别确认（可选）**：对评分靠前的标的拉 30 分钟线，检查突破后回抽低点是否仍在日线 `ZG` 上方，并等待 MACD 金叉。
7. **风控**：输出 `ZG` 下方 1% 的硬止损位，以及回抽低点止损位。

## 安装

```bash
pip install -r requirements.txt
```

## 配置 Tushare

```bash
export TUSHARE_TOKEN=你的token
```

## 运行

先小范围验证：

```bash
python run_screener.py --limit 100 --confirm-30m
```

全市场扫描：

```bash
python run_screener.py --start 20250101 --end 20251101 --confirm-30m
```

只对指定股票池扫描：

```bash
python run_screener.py --codes 300082.SZ,600519.SH --confirm-30m
```

围绕讨论中的奥克股份案例回看（月线 MA20 需要至少 20 个月数据，脚本会自动向前扩展）：

```bash
python run_screener.py --codes 300082.SZ --start 20240101 --end 20251113 --max-days 90
```

或传入 CSV（包含 `ts_code` 列）：

```bash
python run_screener.py --codes my_pool.csv
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--limit` | 只处理前 N 只，适合快速验证 |
| `--sample` | 从全市场随机抽样 N 只，避免只扫到某个板块 |
| `--seed` | 随机抽样种子，默认 42 |
| `--lookback` | 回看自然日数，默认 1200；月线 MA20 与 MACD 至少需要约 1100 天 |
| `--no-monthly-filter` | 关闭月线多头与 MACD 零轴过滤 |
| `--monthly-ma-min-months` | 月线多头排列所需最少月数，默认 24 |
| `--monthly-exclude-current` | 月线MA不含当月未完成K线 |
| `--breakout-pct` | 突破 ZG 的最小幅度，默认 0.01 |
| `--min-strength` | 离开段/前一下跌段力度比，默认 0.5 |
| `--max-days` | 突破高点距今天数上限，默认 30 |
| `--min-daily-pullback-bars` | 日线回抽最少K线数，默认 2 |
| `--min-pullback-bars` | 30分钟回抽最少K线数，默认 3 |
| `--min-stage` | 最低回抽阶段：`breakout_only` / `pullback_above_zg` / `daily_pullback` / `second_launch` |
| `--allow-top-divergence` | 允许突破段出现顶背驰（默认剔除） |
| `--confirm-30m` | 用 30 分钟线确认回抽 |
| `--require-sub` | 只保留 30 分钟确认通过的标的 |
| `--market-filter` | 计算并输出上证指数 MA20 环境 |
| `--refresh` | 强制刷新本地行情缓存 |

## 输出

结果写入 `output/`：

- `candidates_YYYYMMDD_HHMM.csv`：完整字段；
- `candidates_YYYYMMDD_HHMM.md`：按评分排序的摘要。

关键字段：

- `pivot_zg` / `pivot_zd`：中枢上沿/下沿；
- `breakout_date` / `breakout_pct`：突破日与突破幅度；
- `strength_ratio`：突破力度比；
- `pullback_status`：回抽阶段；
- `signal_mode`：`stroke` 表示由完整笔结构识别，`bar` 表示突破仍在进行、用原始K线回退识别；
- `monthly_bull`：月线是否 `MA5 > MA10 > MA20`；
- `monthly_ma5` / `monthly_ma10` / `monthly_ma20`：最新月线的 5/10/20 月均线；
- `pullback_low` / `pullback_bars`：回抽低点与回抽K线数；
- `chase_pct`：现价相对突破高点的涨幅，过大说明位置偏高；
- `stop_zg`：跌破 ZG 下方 1% 的硬止损；
- `sub_ok` / `sub_note`：30 分钟确认结果；
- `score`：排序评分。

## 说明与限制

- 月线过滤默认开启，月线由日线前复权收盘价按月合成；如果 `--start` 太短，脚本会自动向前扩展到约 760 天，以保证 MA20 有足够数据。
- 这是对缠论的工程化简化，不追求严格的递归级别划分；核心是稳定识别“中枢突破 + 回抽不碰 ZG”的结构。
- “隐性上沿/隐性支撑”（扩展中枢形成过程中第一次回落止跌位）暂未单独建模，目前以 ZG 作为核心防守位。
- Tushare 的分钟行情需要积分权限，且部分账号频率限制很严（例如 1 次/小时）；`--confirm-30m` 被限频时会在 `sub_note` 标注失败，但不影响日线筛选。
- 如果日线笔还没走完（刚突破几天的股票），脚本会用原始K线回退识别进行中的突破，`signal_mode=bar`；这类信号需要结合分钟级回抽确认使用。
- 前复权数据在除权除息后会变化，脚本默认按天刷新缓存；批量扫描时可适当增大 `--sleep` 避免触发限频。
- 选股结果只是结构筛选，不构成投资建议。实盘还需结合大盘、板块、基本面和仓位管理。
