# 合同：数据就绪门禁

## 目的

定义未来数据就绪检查流程的输入、输出和判定语义。本文件是规划合同，不是实现代码。

## 命令形态

```text
python scripts/check_data_gate.py --data-root data --mode <research|backtest|paper|live> [--as-of YYYY-MM-DD]
```

## 输入

| 名称 | 必填 | 说明 |
|---|---|---|
| `data-root` | 是 | 数据根目录，包含 raw/bronze/silver/gold/manifest/quality/state 等区域 |
| `mode` | 是 | 就绪模式：research、backtest、paper、live |
| `as-of` | 否 | 用于评估数据新鲜度的日期 |

## 输出字段

```json
{
  "status": "pass | warning | fail",
  "mode": "live",
  "latest_trade_date": "YYYY-MM-DD",
  "expected_trade_date": "YYYY-MM-DD",
  "data_version_id": "string-or-null",
  "blocking_checks": [],
  "warning_checks": [],
  "summary": {
    "datasets_checked": 0,
    "blocking_failures": 0,
    "warnings": 0
  }
}
```

## 必需检查项

| 检查项 | 研究 | 回测 | 模拟 | 实盘 |
|---|---|---|---|---|
| 必需文件存在 | 阻断 | 阻断 | 阻断 | 阻断 |
| 最新交易日新鲜度 | 警告 | 警告 | 阻断 | 阻断 |
| 股票日期重复行 | 阻断 | 阻断 | 阻断 | 阻断 |
| OHLC 关系合法 | 阻断 | 阻断 | 阻断 | 阻断 |
| 必需字段空值 | 阻断 | 阻断 | 阻断 | 阻断 |
| 后复权/未复权覆盖率 | 警告 | 阻断 | 阻断 | 阻断 |
| 后复权/未复权成交额一致 | 警告 | 阻断 | 阻断 | 阻断 |
| execution universe 最新日期 | 警告 | 阻断 | 阻断 | 阻断 |
| QMT/AKShare 覆盖率 | 信息 | 警告 | 警告 | 按数据集角色决定警告或阻断 |
| 财务 PIT 可用性 | 信息 | 警告 | 警告 | 警告 |
| manifest 哈希匹配当前文件 | 阻断 | 阻断 | 阻断 | 阻断 |
| 数据版本 live-ready 标记 | 不适用 | 不适用 | 阻断 | 阻断 |

## 默认阈值

| 指标 | 默认阈值 |
|---|---|
| 全 A 日线主数据覆盖率 | 实盘不低于 99.5%，回测不低于 99.0% |
| 后复权/未复权 `(股票, 日期)` 覆盖率 | 不低于 99.0% |
| 后复权/未复权成交额相对差异 | 超过 0.1% 的行数必须为 0，除非样本被隔离 |
| OHLC 关系错误 | 必须为 0 |
| 必需字段空值 | 必须为 0 |
| 股票日期重复行 | 必须为 0 |
| QMT/AKShare 收盘价相对差异 | 超过 0.5% 记为异常样本 |
| gold 层 execution universe 最新日期 | 实盘和模拟模式必须覆盖最新允许交易日 |

## 新鲜度语义

- 交易日盘前或盘中：实盘模式允许最新行情日期等于上一交易日。
- 交易日收盘后且数据更新窗口结束：实盘模式要求最新行情日期等于当天交易日。
- 非交易日：实盘模式要求最新行情日期等于最近一个交易日。
- 具体盘后更新窗口由后续实现配置，本合同只规定判定方向。

## 状态语义

- `fail`：选定模式下至少一个阻断检查失败。
- `warning`：没有阻断失败，但存在警告。
- `pass`：选定模式下所有必需检查通过。

## 非目标

- 不下单。
- 不修改 QMT 凭证处理。
- 不自动修复数据。
