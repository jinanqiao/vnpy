# 因子补强任务进度交接（2026-07-09）

> 本文档配套 `docs/factor_enhancement_handoff.md` 使用。
> 接手 agent 请先读 CLAUDE.md 和 handoff 文档，再看本文了解已完成/待办状态与关键决策。

## 环境提醒

- 项目 Python 解释器不在 `.venv/`（venv 里只有 python 本体，没装依赖），
  跑测试统一用系统解释器：
  `/Library/Frameworks/Python.framework/Versions/3.13/bin/pytest`
- 用户已开启 `--dangerously-skip-permissions`，可直接执行命令不用弹窗确认

## 数据源关键决策（2026-07-09 讨论产出）

### akshare 探针结论（已探完，2026-07-09 晚间）

完整报告：`data/akshare_probe.md`

- 环境坑：Framework Python 默认走 x86_64，本机 arm64，必须
  `arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3`；
  再显式 `export SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')`
- **东方财富系接口本机全部 RemoteDisconnected**（em 后缀接口全挂），只能用新浪 + 百度
- T5 换手率 = ✅ `stock_zh_a_daily.turnover`（新浪，PIT 正确，2018 起可回溯）
- T5 流通市值 = ✅ `outstanding_share × close`（新浪日频，5500 只票拉一遍 ~45 分钟）
- T5 总市值时序 = 只能靠百度 `stock_zh_valuation_baidu`（月频，颗粒度粗）
- T6 财务 + 公告日 PIT = ✅ `stock_financial_report_sina` 有"公告日期"字段，覆盖 1998 起
- T6 派生指标（ROE / 净利润增长率）= `stock_financial_analysis_indicator` 有 86 字段，
  但**无公告日**，做 PIT 必须与三表 join
- SUE 需自建，akshare 没现成接口

### QMT 网关探针结论（已探完）

- QMT 网关（`vnpy/alpha/research/qmt_gateway_data.py`）当前只暴露：
  `check_qmt_gateway_health` / `fetch_qmt_symbols` / `fetch_qmt_daily_bars` /
  `download_qmt_daily_bars`。**没有市值、股本、换手率、财务字段**
- 财务端点 `/market/financial` 存在但返回空（`data/financial/financial_probe.json`
  显示 8 张表全 `ok=true` 但 `sample_rows=0`）
- 结论：**要用 QMT 拉 T5/T6 需要用户在 Windows 侧改网关加 endpoint**，
  用户明确表示"改网关比较费劲，不想改"

### 数据源方案调整：T5/T6 改走 akshare

- **决策**：T5（市值/规模/真换手率）+ T6（ROE/净利润/公告日）**改用 akshare**，
  不再走 QMT 网关。QMT 网关继续用于日线行情
- **akshare 优势**：
  - 财报接口（`stock_financial_report_sina` 等）一般带**公告日期字段**（PIT 对齐）
  - 不需要改 Windows 侧网关，纯 Python 依赖
- **akshare 主要风险**（akshare 探针 agent 被中途停止，未拿到确切结论）：
  1. 是否有**历史每日流通市值/流通股本时序**（不是仅当日快照）——待验证
  2. 限流与稳定性（走东财/新浪页面爬虫，偶发失败）
  3. 覆盖时间：部分接口只回近 5-10 年
- **接手 agent 待办**：
  1. `pip install akshare`（若未装）
  2. 探测三组关键接口：`stock_zh_a_hist`（含 turnover_rate?）、
     `stock_individual_info_em`（历史市值时序?）、`stock_financial_report_sina`
     （公告日字段?）
  3. 探针结论出来后再决定 T5/T6 具体实现路径

## T1 进度：行业上限约束（**代码已完成，测试全绿**）

### 已完成

1. **配置层**（`vnpy/alpha/research/mainline/config.py`）：
   - 新增字段 `max_per_industry: int = 0`（默认 0 = 不限制，保持逐字节兼容）
   - `__post_init__` 加了非负校验
2. **信号层**（`vnpy/alpha/research/mainline/regime.py:select_stocks_global`）：
   - `max_per_industry <= 0` 走原路径，逐字节等价于 008 后行为
   - `>0` 时：**"先行业内按 score 排前 max_per_industry 只，再全局按 score 取前 regime_top_n"**
     （这是用户明确选定的顺延策略，见 handoff T1 讨论）
   - 用 `cum_sum` 而非 `rank` 做候选池内的全局排名（rank 会把非候选行占用名次导致膨胀）
3. **测试**（`tests/alpha/research/test_mainline_industry_cap.py`，7 项全绿）：
   - 默认值 = 0 / 参数校验 / cap=0 与大 cap 等价性（逐字节回归保护）
   - 手算对照：cap=3 时 A 行业霸榜被压回、名额顺延给 B
   - 复现性（两次运行 parquet 逐字节一致）
   - 直接调用 `select_stocks_global` 的边界测试

### 已完成（2026-07-09 晚间追加）

- ✅ **全量 pytest**：`132 passed`（125 老 + 7 新 T1）
  跑法：`arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/alpha/research/ -q`
- ✅ **三档回测对照**（生产风控组合，2022-05~2026-07 = 50 期）：
  报告落 `specs/010-industry-cap/spec-and-acceptance.md`
  - cap=0（基线）：年化 15.49% / MDD -19.21% / Sharpe 0.985 —— 与 CLAUDE.md 记录逐字节一致 ✓
  - cap=6：年化 15.31% / MDD -17.95% / Sharpe 0.987 （回撤改善 1.27pp，年化 -0.18pp）
  - cap=8：年化 15.90% / MDD -19.87% / Sharpe 1.003 （年化 +0.41pp，回撤反而恶化 0.66pp）
  - 三档差异都在噪声量级，**没有明确打赢基线**，`max_per_industry=0` 保持默认
  - 每期最大行业占比：cap0 均值 10.12/最坏 19（33.7% / 63.3%），cap6 严格封顶 6，cap8 均值 7.78
- CLI 坑（写进 spec 的"已知踩坑"）：
  1. `run_mainline_signals.py` 默认 `--rebalance-freq weekly` 会硬覆盖 config-json，
     跑 monthly 必须命令行显式指定
  2. argparse 对负数：`--score-weights` 传负数必须用等号形式 `--score-weights='-0.40,...'`

### 顺延策略明确记录（用户选定）

`max_per_industry > 0` 时的两种可能实现：
- ✅ **已选**：先各行业内排前 max 只（形成候选池），再全局按 score 取前 N
- ❌ 未选：全局按 score 顺序扫，某行业达上限就跳过，直到凑够 N

## T2–T6 待办（原 handoff 未开工）

- **T2** MAX + Amihud 因子检验（现有数据可做，两个因子可并行）
- **T3** 因子健康度季检脚本
- **T4** 滚动窗口样本外验证
- **T5** 规模因子（改走 akshare）
- **T6** 财务因子（改走 akshare）

## 并行化建议（已讨论）

- **可并行**：
  - T2 两个因子的检验脚本（独立输入 → 独立报告）
  - T5/T6 akshare 数据下载（不同接口互不干扰）
  - T2 / T5 / T6 的多因子截面检验（模板一致）
- **不建议并行**：
  - 改代码类（T1、T2b 接入 stocks.py）——同文件冲突
  - T4 多轮回测（会吃内存）
  - QMT 网关首次拉数据（冷启动，多进程并打容易全挂）

## 立即可执行的下一步（接手 agent 建议顺序）

**T1 + akshare 探针已收工**（见上面"已完成"章节）。下一步：

1. **T2 因子检验**（可开工）：
   - 抄 `scripts/validate_stock_factors.py` 写 `validate_lottery_liquidity_factors.py`
   - MAX = 近 20 日最大单日涨幅（预期负 IC）
   - Amihud = 近 20 日 mean(|日收益|/成交额)（预期正 IC）
   - 闸门：|t| >= 2 且与现有因子截面相关 < 0.5
   - 报告落 `outputs/factor_checks/`
2. **T5/T6 数据侧**：
   - 用户如要开工，先写 `scripts/download_akshare_daily.py`（一次落 parquet 到
     `data/akshare/daily_bars_outstanding.parquet`，全 A 拉一遍 ~45 分钟）
   - T6 财务：`scripts/download_akshare_financial.py`（三表 + 公告日，全 A ~3 小时）
   - **注意**：跑之前必须 `export SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')`
     和 `arch -arm64` 前缀（见 `data/akshare_probe.md` 顶部）
