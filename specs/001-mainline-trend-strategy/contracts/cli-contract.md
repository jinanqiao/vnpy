# CLI Contract: `scripts/run_mainline_signals.py`

**Date**: 2026-07-08　对应 SC-001（一条命令产出全部结果）

## 调用形式

```bash
python scripts/run_mainline_signals.py \
    --start 2022-03-01 \
    --end 2026-06-30 \
    --output-dir outputs/mainline \
    --name all_a_default
```

## 参数

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--start` / `--end` | 否 | 数据可用完整区间 | 调仓日筛选范围（含端点）；单日模式传相同值 |
| `--data-dir` | 否 | `data` | 数据湖根目录 |
| `--output-dir` | 否 | `outputs/mainline` | 产物根目录 |
| `--name` | 否 | `mainline_signals` | 实验名，run_id = `<时间戳>_<name>` |
| `--universe` | 否 | `all_a` | `all_a` / `leaders`（沪深300+中证500） |
| `--config-json` | 否 | — | JSON 文件覆盖 MainlineConfig 任意字段（FR-013） |
| `--equal-weights` | 否 | false | 打分权重切换为三项等权对照（FR-010） |

## 行为契约

1. 退出码 0 = 全部请求范围内的调仓日处理完成（含合法的空清单期）；非 0 = 数据缺失、校验失败或参数非法，stderr 给出原因。
2. 产物写入 `<output-dir>/<run_id>/`，文件集与 schema 见 [artifacts-schema.md](./artifacts-schema.md)；同一 run_id 目录不覆盖复用。
3. 相同输入数据 + 相同配置 → 逐字节一致的 parquet/JSON 输出（SC-004）；实现约束：固定排序键、不使用集合遍历顺序、时间戳仅出现在 run_id 与 manifest 的 generated_at 字段。
4. 信号计算仅使用 `rebalance_date` 当日及之前的数据（FR-012），任何窗口不足的股票/行业按 spec Edge Cases 处理并留档，不中断运行。
5. 日志：INFO 级输出逐调仓日进度（日期、主线行业数、入选个股数）；数据质量事件记入 manifest 而非仅日志。

## 库层入口（供 notebook / 后续回测复用）

```python
from vnpy.alpha.research.mainline import MainlineConfig, run_mainline_signals

result = run_mainline_signals(config)   # MainlineConfig -> dict[str, pl.DataFrame] + 产物目录
```

返回值暴露 `industry_signals` / `stock_signals` / `selection` 三个 polars DataFrame 与 `output_dir` 路径，字段与产物 schema 一一对应。子包自包含，不依赖任何既有研究模块。
