# 选股因子补强任务交接（2026-07-09）

> 交接对象：Claude Code（或任何接手的 agent）。开工前先读仓库根目录 CLAUDE.md
> 了解代码规范、复现性约束和"先检验后接入"闸门。本文档只列任务本身。

## 背景一句话

当前策略是"全市场反转选股（3 个价量因子）+ 逐日 MA60 择时"，样本内年化 15.5%。
短板：三个因子同源（全是价量派生）、行业集中度无约束（最差一期 19/30 只同行业）、
因子方向无监控（反向权重是 2021-2025 样本内结论，2026 年 rs 已翻正）。

## 任务清单（按顺序做，每项独立可交付）

### T1 行业上限约束（确定性收益，先做）

- 信号层加配置 `max_per_industry: int = 0`（0=不限制，保持现状默认）
- none/regime 模式全池排名时：单一行业入选超过上限后，名额顺延给下一名
- 改 `vnpy/alpha/research/mainline/regime.py` 的 select_stocks_global
- 测试：手算对照（构造一个行业霸榜的合成数据）+ 上限 0 时逐字节回归
- 回测对照：上限 6/8/不限 三档，生产风控组合（见 CLAUDE.md），
  报告年化/回撤/Sharpe + 每期最大行业占比分布，落 specs/010-*/

### T2 MAX 彩票因子 + Amihud 非流动性检验（现有数据可做）

- 新脚本 scripts/validate_lottery_liquidity_factors.py，模板抄
  scripts/validate_stock_factors.py（同一套 Rank IC + 5 分层 + 与 ret_60 相关性）
- MAX = 近 20 日最大单日涨幅（预期负 IC：彩票股跑输）
- Amihud = 近 20 日 mean(|日收益| / 成交额)（非流动性溢价，预期正 IC）
- 闸门：|t| >= 2 且与现有因子截面相关 < 0.5 才进入 T2b，否则记录结论收工
- T2b（若通过）：仿照 volatility_weight 的方式接进 stocks.py（默认 0 关闭），
  跑权重两档回测对照，赢了基线才建议启用

### T3 因子健康度季检脚本

- 新脚本 scripts/check_factor_health.py：对生产中的 rs/nh/vol 三因子跑
  滚动 12 期 Rank IC，输出当前方向 vs 生产权重方向是否一致；
  不一致时 exit code 非 0 + 醒目告警（用户会定期手动跑）
- 顺带输出最近 4 期每期的 IC，让用户看得到趋势

### T4 滚动窗口样本外验证

- 新脚本或 notebook：前 36 个月定参（用当前生产参数即可，不重新寻优）、
  后 12 个月验证，滚动推进；报告样本外各窗口年化/回撤 vs 样本内的衰减幅度
- 这是给用户"能不能上实盘"的核心证据，报告写清楚不要美化

### T5 市值数据 + 规模因子（需要 QMT 网关在线，先问用户开机）

- 探测 QMT 网关有无总股本/流通股本接口（参考 scripts/download_sw1_members.py
  的连接方式；网关冷启动慢，120s 超时 + 重试）
- 下载入湖 data/reference/，更新 data/dictionary/data_dictionary.md
- 规模因子（对数流通市值）+ 真换手率因子检验，同一套闸门

### T6 财务数据 + 质量/成长因子（最大不确定项，最后做）

- 先探测 QMT 财务接口是否提供**公告日期**（data/financial/financial_probe.json
  有早期探测记录）。没有公告日就只做"滞后两季"的保守 PIT 对齐并在报告声明
- 候选因子：ROE、净利润同比增速、SUE；同一套检验闸门

## 验收口径（每个任务通用）

1. `python3 -m pytest tests/alpha/research/ -q` 全绿（当前 125 项）
2. 新开关默认关，配"关闭时逐字节回归"测试
3. 实验数字落 specs/0XX-*/spec-and-acceptance.md，检验报告落 outputs/factor_checks/
4. 不改动当前生产配置的默认值，除非对照回测明确打赢
   （基线：年化 15.5% / 回撤 -19.2% / Sharpe 0.98，2022-03~2026-06）
