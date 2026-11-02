# 202611-stock

11月的想法：基于缠论“日线中枢突破 + 次级别回抽三买”的 A 股选股脚本，数据来自 Tushare。

## 实现的逻辑

对应讨论中的方案：

1. **日线中枢**：连续三笔重叠构成中枢，得到 `ZG`（上沿）、`ZD`（下沿）。
2. **向上离开段**：一笔向上突破 `ZG`，并检查：
   - 突破幅度 >= `--breakout-pct`；
   - 离开段力度 >= 前一下跌段的 `--min-strength`（默认 0.5）；
   - 离开段没有明显顶背驰。
3. **回抽不碰中枢**：突破高点之后的所有日线低点必须严格在 `ZG` 之上，否则视为假突破/中枢延伸。
4. **回抽结构**：
   - `breakout_only`：刚突破，尚未形成明确回抽；
   - `pullback_above_zg`：日线回抽低点在 `ZG` 上方；
   - `daily_pullback`：日线走出完整回抽笔且不碰 `ZG`；
   - `second_launch`：回抽后已经出现向上笔。
5. **次级别确认（可选）**：对评分靠前的标的拉 30 分钟线，检查突破后回抽低点是否仍在日线 `ZG` 上方，并等待 MACD 金叉。
6. **风控**：输出 `ZG` 下方 1% 的硬止损位，以及回抽低点止损位。

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

围绕讨论中的奥克股份案例回看：

```bash
python run_screener.py --codes 300082.SZ --start 20250601 --end 20251113 --max-days 90
```

或传入 CSV（包含 `ts_code` 列）：

```bash
python run_screener.py --codes my_pool.csv
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--breakout-pct` | 突破 ZG 的最小幅度，默认 0.01 |
| `--min-strength` | 离开段/前一下跌段力度比，默认 0.5 |
| `--max-days` | 突破高点距今天数上限，默认 30 |
| `--min-pullback-bars` | 回抽最少K线数，默认 3 |
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
- `pullback_low`：回抽低点；
- `stop_zg`：跌破 ZG 下方 1% 的硬止损；
- `sub_ok` / `sub_note`：30 分钟确认结果；
- `score`：排序评分。

## 说明与限制

- 这是对缠论的工程化简化，不追求严格的递归级别划分；核心是稳定识别“中枢突破 + 回抽不碰 ZG”的结构。
- “隐性上沿/隐性支撑”（扩展中枢形成过程中第一次回落止跌位）暂未单独建模，目前以 ZG 作为核心防守位。
- Tushare 的分钟行情需要积分权限；没有权限时 `--confirm-30m` 会标注失败，但不影响日线筛选。
- 前复权数据在除权除息后会变化，脚本默认按天刷新缓存；批量扫描时可适当增大 `--sleep` 避免触发限频。
- 选股结果只是结构筛选，不构成投资建议。实盘还需结合大盘、板块、基本面和仓位管理。
