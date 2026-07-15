# Data Model: 主线策略风控层

**Date**: 2026-07-09　基于 002 既有 schema 的增量定义，未列出的实体不变。

## 1. BacktestConfig 新增字段（并入既有 frozen dataclass）

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| timing_enabled | bool | False | 大盘择时开关（FR-001） |
| timing_ma_window | int | 60 | 择时均线窗口（交易日数） |
| no_signal_exit_enabled | bool | False | 无信号月清仓开关 |
| stop_loss_enabled | bool | False | 个股止损开关 |
| stop_loss_rate | float | 0.15 | 止损阈值（较买入执行价的回撤比例，> 0） |

约束：`stop_loss_rate <= 0` 或 `timing_ma_window < 2` 时构建配置直接报错（ValueError）。
默认值全部等价于 002 现状行为（FR-001/FR-008）。

## 2. TradeRecord.reason 枚举扩展（不加新列，见 research R2）

```
suspended / limit_up / limit_down / delisted / insufficient_cash   # 002 既有
timing_exit      # 择时清仓卖出（status=filled 或 deferred）
no_signal_exit   # 无信号月清仓卖出（status=filled 或 deferred）
stop_loss        # 个股止损卖出（status=filled 或 deferred）
```

不变式增补：
- 风控卖出（reason ∈ 新增三值）只出现在 side=sell 的行
- 普通调仓卖出 reason 仍为 null；abandoned 行的 reason 仍限于 002 既有枚举
- 全部开关关闭时，新增三值在 trades 中出现次数为 0（回归保护）

## 3. 数据质量日志新增事件类型（data_quality.json）

| type | 触发 | 字段 |
|---|---|---|
| timing_skip | 择时判定弱市，跳过该期买入 | signal_date, benchmark_close, benchmark_ma, detail |
| timing_window_short | 均线窗口不足，视为通过 | datetime, available_days, detail |
| no_signal_exit | 无信号月清仓执行 | month_end, exit_date, n_positions, detail |
| stop_loss_trigger | 止损触发（记录触发日，执行见 trades） | datetime, vt_symbol, drawdown, detail |

## 4. 对照实验汇总（comparison.md，人读文件）

四组固定组合：

| 组名 | timing | no_signal_exit | stop_loss |
|---|---|---|---|
| baseline | off | off | off |
| timing | on | off | off |
| timing_ns | on | on | off |
| all_on | on | on | on |

汇总表每组一行：组名、开关状态、总收益、年化、最大回撤、夏普、月度胜率、年均换手、成本拖累、产物目录；
另附"边际贡献"段：相邻组两两对比（timing−baseline、timing_ns−timing、all_on−timing_ns）。

## 5. 派生数据（运行期，不落盘）

- **基准均线序列**: benchmark 收盘的滚动均值（窗口 timing_ma_window），与 nav 估值日历对齐，取执行日前一交易日的值做判定（research R1）
- **无信号月末集合**: 交易日历真实月末 − selection 信号月末，限定 [首个信号月, 行情末日]（research R4）
- **止损待卖队列**: {vt_symbol: (触发日, reason)}，并入既有 pending_sells 机制执行（research R5）
