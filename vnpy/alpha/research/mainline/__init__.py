"""主线强势股月度选股信号（自包含子包）。

## 这个包是干什么的

全市场选股信号（008 起的默认形态）：每个调仓日在全市场做两项必要过滤
（上市满一年 + 当日可交易），再用三个因子打分选出前 regime_top_n 只——
距 52 周新高正向使用，相对强度与量价反向使用（近 5 年 Rank IC 为负，短期反转效应）。
只生成信号（选股清单），不含回测和仓位风控（另行立项）。

行业层与趋势过滤保留为可切换的对照配置（基于因子检验的改造，见 outputs/factor_checks/）：
    industry_mode:   none（默认，不要行业层，逐期全市场选股）
                     / select（先选主线行业再行业内选股，旧默认）
                     / regime（行业层只当"有没有主线"的市场状态开关）
    industry_source: gics1（11 个粗行业）/ sw1（31 个申万一级行业），仅 select/regime 用
    trend_filters_enabled: False（默认，只留两项必要过滤）/ True（恢复五项硬过滤）

## 阅读指南（建议按顺序读，总共 6 个文件）

| 顺序 | 文件 | 回答的问题 | 对应 spec 需求 |
|---|---|---|---|
| 1 | config.py | 策略有哪些参数、默认值是多少 | FR-013 |
| 2 | data_loader.py | 数据从哪来、有什么质量问题 | FR-006 数据部分、R3 |
| 3 | industry.py | （对照配置用）主线在哪几个行业、主线是第几个月 | FR-001 ~ FR-003、004 特性 |
| 4 | stocks.py | 过滤和打分规则（含反向权重与简化过滤） | FR-004 ~ FR-011、008 调整 |
| 5 | regime.py | 全市场选股怎么做（none/regime 模式的个股层） | 006/008 特性 |
| 6 | pipeline.py | 整个流程怎么跑起来、结果存在哪 | FR-012 ~ FR-014 |

设计约定：整个包只有 MainlineConfig 一个类，其余全是"输入 DataFrame、
输出 DataFrame"的纯函数；不依赖本仓库任何其他研究模块，可整体拷走阅读。

## 快速上手

    from vnpy.alpha.research.mainline import MainlineConfig, run_mainline_signals

    result = run_mainline_signals(MainlineConfig())
    print(result["selection"])          # 入选清单
    print(result["output_dir"])         # 产物目录

命令行方式见 scripts/run_mainline_signals.py。
"""

from .config import MainlineConfig, load_config_from_json
from .pipeline import run_mainline_signals

__all__ = ["MainlineConfig", "load_config_from_json", "run_mainline_signals"]
