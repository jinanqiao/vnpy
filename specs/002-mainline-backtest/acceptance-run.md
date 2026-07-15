# Acceptance Run: 主线强势股策略回测（002-mainline-backtest）

**Date**: 2026-07-08
**结论**: 全部验收项通过。策略历史表现为负（详见下文），属于如实呈现的研究结论，非实现缺陷。

## 自动化测试

```
python -m pytest tests/alpha/research/test_backtest_execution.py \
                 tests/alpha/research/test_backtest_portfolio.py \
                 tests/alpha/research/test_backtest_pipeline.py -q
→ 23 passed

python scripts/validate_alpha.py
→ import-check / pytest-alpha (208+ passed) / py-compile-alpha 全部 OK
```

## T009: 数据湖升级（US4 / 场景 0）

- 第一轮下载（`adjust="back"` 等额后复权，40 分钟）被校验拦截：等额复权把高分红老股票
  的早年价格调成负数（如 600136.SH 出现 -0.16 元），复权比例阶梯性校验 105 万行不通过。
  **教训已固化**：脚本改用 `back_ratio` 等比后复权（涨跌幅不变、价格恒为正）。
- 第二轮下载（`--workers 8` 并发，30 分钟）完成后合并 1478 万行；3 只退市股
  （600193.SH / 600608.SH / 605081.SH）在等比模式下返回全 0 价格，被整只剔除并记入失败清单。
- 最终校验：覆盖率 99.94%（≥99%）、成交额一致 0 行异常、复权比例 0 行回落 → **通过**，
  原子写入 `data/normalized/daily_bars_all_a_adjusted.parquet`（5203 只 / 14,772,246 行）。
- 失败清单 4 只（1 只下载失败 + 3 只无效数据）= 0.08% ≤ 1% 阈值。
  报告见 `data/quality/adjusted_bars_verification.md`。

## T018: 真实数据全历史回测（US1 / 场景 1、2、5）

输入: `outputs/mainline/20260708_180215_all_history_final/selection.parquet`
（28 个调仓期，2022-07-29 ~ 2026-04-30）
产物: `outputs/mainline_backtest/20260708_200145_full_history/`

- 运行耗时 **3 秒**（SC 要求 ≤ 5 分钟）；949 个交易日；六件套齐备。
- 场景 2 净值复算：全部 949 天 `nav×capital = cash + Σ市值`，最大误差 2.3e-10 元。
- 场景 5 报告可读性：report.md 含核心指标表（对比沪深300）、分年度表、
  成本拖累、异常交易汇总、四条固定局限性声明。

| 指标 | 组合 | 沪深300 |
|---|---|---|
| 总收益 | -31.37% | +14.89% |
| 年化收益 | -9.52% | +3.76% |
| 最大回撤 | -52.81% | -25.08% |
| 夏普 | -0.18 | 0.30 |

月度胜率 42.9%（跑赢基准 57.1%），年均换手 5.4 倍，成本拖累 1.97%/年。
分年度：2022/2023/2026 大幅跑输，2025 (+35.25%) 跑赢。动量策略在熊市/震荡市
回撤失控是符合逻辑的呈现，改进方向（择时/止损/仓位控制）属后续特性。

## T021: 交易可追溯抽查（US2 / 场景 3）

- 交易 1106 笔（filled 1100 / abandoned 6 / deferred 0 / forced 0）。
- 随机抽 10 笔 filled：signal_date / planned_date / executed_date / 执行价 /
  股数（均为 100 整数倍）/ 佣金 / 印花税（仅卖出）/ 滑点 三分项齐全。
- 抽 abandoned 1 笔：2024-11-29 002031.SZ reason=limit_up，金额字段全 null。
- 全部买入记录的 (signal_date, vt_symbol) 均能回溯到 selection.parquet。
- 本次真实区间无 deferred/forced 案例；两类路径已由合成数据测试覆盖
  （test_backtest_pipeline 端到端注入停牌场景）。

## T023: 参数对照实验（US3 / 场景 6）

| 组 | 年化收益 | cost_drag |
|---|---|---|
| 零成本 | -7.61% | 0.0000% |
| 默认成本 | -9.52% | 1.9669% |
| 3 倍滑点 (0.003) | -11.73% | 4.4784% |

方向性断言通过（成本越高年化越低）；`--zero-cost` 时 nav == nav_gross；
config.json 正确快照 slippage_rate=0.003；未知字段报错由测试覆盖。

## T026: 可复现性（场景 4）

同参数连跑两次（repro_a / repro_b），nav/positions/trades 三个 parquet
SHA-256 完全一致。

## 可读性自查（T025）

7 个模块文件全部 ≤ 300 行（最大 pipeline.py 295 行）；除 BacktestConfig 外
无类、无继承/装饰器/生成器；公开函数均有中文 docstring；阅读指南见
`vnpy/alpha/research/mainline_backtest/__init__.py`。
