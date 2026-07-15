# 验收运行记录（T009 / T018 / T021）

**日期**: 2026-07-08　**环境**: macOS, python3.13, polars 1.42.1　**数据湖**: data/（截至 2026-07-02）

## 自动化测试

- `tests/alpha/research/test_mainline_industry.py` 5 通过（行业动量/广度手算对照、双窗口门槛、广度剔除、不硬凑 top_n、小行业排除、历史不足空快照）
- `tests/alpha/research/test_mainline_stock.py` 6 通过（均线四种破坏形态、乖离/新高/可交易/上市时长、三因子手算、量比截断、加权总分复算、每行业选取规则）
- `tests/alpha/research/test_mainline_pipeline.py` 7 通过（GICS1 过滤、缺列报错、调仓日历、除权侦测、未知配置字段报错、端到端六件套与全部不变量、两次运行 parquet 逐字节一致）
- `python scripts/validate_alpha.py` 全量门禁通过（186 passed, 1 skipped）

## Quickstart 场景

| 场景 | 命令/产物 | 结果 |
|---|---|---|
| 1 单期冒烟 | `--start 2026-05-25 --end 2026-06-05 --name smoke` | 退出码 0，六件套齐备；2026-05-29 无主线行业（空清单也是合法信号） |
| 2 行业人工评审 | all_history 最近一期 2026-04-30 | 入选 GICS1能源（rank_60=1, rank_20=3, breadth=0.72）；公用事业因 rank_20=10 被 rank_gate 拒绝；原材料/信息技术动量达标但 breadth≈0.40 被 breadth 拒绝——"动量定强弱、广度验真伪"逻辑符合预期 |
| 3 个股复核 | 同期能源行业前 10 | 全部 filter_*=true、score 复算最大误差 0.0；抽查被剔除个股原因均属固定枚举（ma_align 等） |
| 4 可复现性 | repro_a / repro_b（2025H1） | 三个 parquet SHA-256 完全一致 |
| 5 全历史性能 | `--name all_history_final` | 52 期（2022-03 ~ 2026-06），耗时约 13 秒（约束 ≤ 5 分钟）；首个可用调仓日 2022-03-31（此前 3 期窗口不足已记录跳过） |
| 6 参数覆盖 | `--equal-weights` / `--config-json {"industry_top_n":5}` | config.json 分别显示 (1/3,1/3,1/3) 与 industry_top_n=5，产物结构不变 |

## 附加验证

- 龙头池：`--universe leaders --start 2026-01-01 --end 2026-06-30` 正常运行（800 只成分股，6 期均有信号）
- 数据质量日志量级合理：疑似除权 500 条（上限截断）、多行业归属 200 条（上限截断）、无主线 25 期、窗口不足 3 期、行业入选不足下限 3 条

## 可读性自查（T020）

- 单文件行数：config 92 / data_loader 166 / industry 207 / pipeline 204 / stocks 278，全部 ≤ 300
- 全包仅 `MainlineConfig` 一个类（frozen dataclass），无继承、无装饰器（除 dataclass）、无生成器
- 每个公开函数均有中文 docstring，`pipeline.py` 自上而下线性可读
- 不 import 任何 turtle_* / pit_universe / data_check / run_summary 模块

## 已知局限（与 research.md 一致）

行业成分为当前快照（R2）；价格未复权，除权日附近动量/均线信号可能失真（R3），每份 report.md 均携带固定声明。
