# CLI Contract: 主线回测的两个命令行入口

## 1. scripts/download_adjusted_bars.py（US4 数据湖升级）

```bash
python scripts/download_adjusted_bars.py [选项]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| --output-path | data/normalized/daily_bars_all_a_adjusted.parquet | 最终产物路径 |
| --shard-dir | data/raw/qmt/adjusted | 分片暂存目录 |
| --symbols-path | data/universe/all_a_symbols.parquet | 股票清单来源 |
| --batch-size | 200 | 每批下载只数 |
| --count | 5000 | 每只回溯的 K 线根数 |
| --timeout | 90 | 单请求超时（秒） |
| --resume | （开关） | 跳过已存在的分片，断点续传 |
| --skip-verify | （开关） | 跳过与未复权数据的一致性校验（调试用） |

**行为契约**：
1. 启动先做网关健康检查；失败立即退出码 2，打印排查提示（确认阿里云实例运行、安全组放行 8710、`QMT_GATEWAY_URL` 环境变量指向正确 IP）
2. 逐批下载 `adjust="back"`，每批写一个分片；单只失败不中断，记入 `data/quality/failed_symbols_adjusted.json`
3. 全部批次结束后合并分片 → 执行 R8 三项校验 → 校验通过才原子写入 `--output-path`；校验失败退出码 3，不写最终文件
4. 校验报告写 `data/quality/adjusted_bars_verification.md`
5. 退出码：0 成功 / 2 网关不可达 / 3 校验失败 / 1 其他错误

## 2. scripts/run_mainline_backtest.py（US1~US3 回测）

```bash
python scripts/run_mainline_backtest.py --selection <path> [选项]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| --selection | （必填） | 001 产物 selection.parquet 路径 |
| --start / --end | 空 | 回测的调仓期范围（含），如 2023-01-01 |
| --data-dir | data | 数据湖根目录 |
| --output-dir | outputs/mainline_backtest | 产物根目录 |
| --name | mainline_backtest | 实验名（拼进 run_id） |
| --initial-capital | 1000000 | 初始资金 |
| --zero-cost | （开关） | 三项成本全部置 0（对照实验；正常运行已内置零成本影子净值） |
| --config-json | 空 | JSON 覆盖任意 BacktestConfig 字段（未知字段报错） |

**行为契约**：
1. 输入文件缺失/缺列 → 退出码 1，报错信息指明文件与缺列
2. 后复权文件不存在 → 退出码 1，提示先运行 `download_adjusted_bars.py`
3. 正常完成 → 退出码 0，产物写 `outputs/mainline_backtest/<时间戳>_<name>/`（六件套见 artifacts-schema.md），INFO 日志逐期打印"调仓期 / 成交 / 放弃 / 延迟 / 期末净值"
4. selection 里的调仓期在行情范围外 → 跳过并记录，不报错
