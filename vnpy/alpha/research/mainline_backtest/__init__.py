"""主线强势股策略回测（自包含子包）。

## 这个包是干什么的

把 001 特性产出的月度选股清单（selection.parquet）变成可度量的历史业绩：
模拟"信号日次一交易日开盘、全部入选个股等权、含佣金/印花税/滑点"的真实
调仓过程，产出净值曲线、风险指标和沪深300 对比。只依赖 selection 文件，
不 import 信号包的任何代码（两个特性通过文件契约衔接，可独立演进）。

## 阅读指南（建议按顺序读，总共 8 个文件）

| 顺序 | 文件 | 回答的问题 | 对应 spec 需求 |
|---|---|---|---|
| 1 | config.py | 回测有哪些参数、默认值是多少 | 002 FR-013 |
| 2 | data_loader.py | 数据从哪来、后复权数据怎么校验 | 002 FR-001/011/011a |
| 3 | execution.py | 调仓日钱怎么换成股票、为什么有的换不成 | 002 FR-002~006 |
| 4 | portfolio.py | 每天收盘组合值多少钱 | 002 FR-007 |
| 5 | risk.py | 什么时候该减仓（择时/无信号清仓/止损） | 003 FR-001~007 |
| 6 | metrics.py | 这条净值曲线好不好 | 002 FR-008~010 |
| 7 | pipeline.py | 整个回测怎么跑起来、结果存哪 | 002 FR-012 |
| 8 | report.py | 指标怎么写成人读的成绩单 | 002 FR-009/012 |

风控层（003 特性）默认全部关闭，打开方式见 scripts/run_mainline_backtest.py
的 --timing / --no-signal-exit / --stop-loss 参数；四组对照实验一键跑见
scripts/run_risk_experiments.py。

设计约定：整个包只有 BacktestConfig 一个类，其余全是纯函数；
持仓/交易记录用普通字典表达；固定排序保证同输入结果逐字节一致。

## 快速上手

    from vnpy.alpha.research.mainline_backtest import BacktestConfig, run_mainline_backtest

    config = BacktestConfig(selection_path="outputs/mainline/<run_id>/selection.parquet")
    result = run_mainline_backtest(config)
    print(result["metrics"]["annual_return"])   # 年化收益
    print(result["output_dir"])                 # 产物目录

命令行方式见 scripts/run_mainline_backtest.py；
后复权数据下载见 scripts/download_adjusted_bars.py。
"""

from .config import BacktestConfig, load_config_from_json, zero_cost
from .pipeline import run_mainline_backtest

__all__ = ["BacktestConfig", "load_config_from_json", "run_mainline_backtest", "zero_cost"]
