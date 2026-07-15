# 项目约定（主线策略研究，vnpy/alpha/research/mainline*）

## ⚠️ 实盘准入警告（2026-07-15 定）

**当前不允许上实盘。** 关键缺陷：

1. **因子层未过闸门**：三个生产因子（rs_60/nh_252/vol_ratio）零个通过 IC 显著 + 分层单调
   双重检验；`outputs/factor_checks/stock_factors_gics1.md` 自己写"过去正贡献几乎全部
   来自大盘择时与无信号清仓，而非选股"。
2. **实盘 pre-trade 风控几乎为空**：place_order 前不查涨跌停、停牌、ST、单股仓位、单日
   损失。回测里 `check_buy_blocked/check_sell_blocked` 逻辑完整，但未搬到实盘链路。
3. **执行桥完全缺失**：`live.signals` / `live.target_positions` 表建好了但无写入代码；
   没有 `selection.parquet → orders` 转换脚本。
4. **参数同一段样本反复调**：spec-009 自己警告"过拟合风险在累积"。

**保险丝**：`qmt_gateway_trade.place_order` 前置门锁 `LIVE_TRADING_ENABLED`；默认
未设 → 抛 `LiveTradingDisabledError` 拒单。真要上盘先跑完 optimization_plan_v1.md
的 Phase 1~3（含 60 天纸盘验证）。

---

本仓库在 vnpy 框架上做 A 股量化策略研究。当前活跃工作全部围绕
`vnpy/alpha/research/mainline`（选股信号）和 `mainline_backtest`（回测引擎）。
用户是 Python 初学者，代码必须保持初学者可读。

## 硬性代码规范

- 单文件 ≤ 300 行；除 Config 类外只写"输入 DataFrame → 输出 DataFrame"的纯函数
- 数据处理只用 polars（不用 pandas）；文档字符串和注释用中文，讲清"为什么"
- 两个子包自包含，不得依赖仓库里其他研究模块
- **逐字节可复现**是硬约束：同输入两次运行产物必须逐字节一致。涉及浮点求和的
  polars 聚合前必须显式 sort 并 group_by(maintain_order=True)；逐笔记账循环内
  更新现金/成本（不许先求和再一次性加）
- 新功能一律做成默认关闭的开关，旧行为必须有"关闭时逐字节回归"测试保护
- 已钉住旧行为的测试不许改语义（它们钉的是历史 spec，见各测试文件头注释）

## 工作流程约定

- **先检验后接入**：任何新因子先写离线检验（Rank IC + 分层，模板见
  scripts/validate_stock_factors.py / validate_rotation_factors.py），t 值不显著
  就不接入策略。历史上行业动量、MACD/RSI、轮转因子都倒在这道闸门前
- 每个特性完成后在 specs/00X-*/ 落一份 spec-and-acceptance.md（含实验数字）
- 检验报告写进 outputs/factor_checks/*.md
- 新 py 文件加进 scripts/validate_alpha.py 的 PY_COMPILE_TARGETS
- 跑测试：`python3 -m pytest tests/alpha/research/ -q`（当前 125 项全绿）

## 当前生产配置（2026-07-09 定版，样本内年化 15.5% / 回撤 -19.2% / Sharpe 0.98）

- 信号：`industry_mode=none`（无行业层，全市场选前 30）+ 反向权重
  score_weights=(-0.40, 0.35, -0.25)（rs/vol 反向 = 反转策略）+ 简化过滤
  （trend_filters_enabled=False）+ 月频 + mom_window=60, confirm_window=20
- 回测：逐日 MA60 择时 + timing_reentry（逐日再入场）+ no_signal_exit +
  止损 20% + delta_rebalance
- 运行示例：
  `python3 scripts/run_mainline_signals.py --rebalance-freq monthly --config-json <60/20窗口> ...`
  `python3 scripts/run_mainline_backtest.py --selection <path> --timing --timing-daily --timing-reentry --no-signal-exit --stop-loss --delta-rebalance`

## 数据湖要点

- 行情：data/normalized/daily_bars_all_a_adjusted.parquet（等比后复权 back_ratio，
  算收益）+ daily_bars_all_a.parquet（未复权，算整手股数）
- QMT 网关（阿里云）默认 IP 见 vnpy/alpha/research/qmt_gateway_data.py 的
  DEFAULT_ALIYUN_PUBLIC_IP；网关冷启动慢，首次请求要 120s 超时 + 重试；
  下复权数据必须用 adjust="back_ratio"（"back" 是加法复权会出负价）
- 行业：sector_members.parquet（GICS1，11 个）/ sw1_members.parquet（申万 31 个），
  均为当前时点快照（有前视偏差，报告里要声明）
- 关键历史结论索引：specs/008（因子反向+去行业层）、specs/009（差量调仓/低波因子/
  MA 敏感性）、outputs/factor_checks/（全部因子检验数字）

## 已知踩坑（别再踩）

- polars 并行 join 后行序不稳定 → 浮点求和顺序漂移 → 复现性破坏（先 sort 再聚合）
- 财务数据若做 PIT 必须用公告日对齐，用报告期 = 未来数据
- 单因子 IC 显著 ≠ 组合里加分（低波因子 IC -0.085 显著，接入后年化掉 6pp——
  反转策略赚的就是高波反弹）
- 择时"逐日清仓"必须配"逐日再入场"，只做一半会在牛市大幅踏空
