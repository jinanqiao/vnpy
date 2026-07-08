# 股票版海龟策略优化改造计划

本文目标：把当前项目改造成一条可以执行、可以验证、可以画图、可以面试讲清楚的股票版海龟策略研究链路。

## 当前已落地状态

已新增一条本地可验证的最小闭环：

- 数据检查：`vnpy/alpha/research/data_check.py`
- 数据读取和字段标准化：`vnpy/alpha/research/turtle_data.py`
- 股票池和可交易过滤：`vnpy/alpha/research/turtle_universe.py`
- Donchian 通道和 ATR：`vnpy/alpha/research/turtle_indicators.py`
- 海龟入场/离场信号：`vnpy/alpha/research/turtle_signals.py`
- 简化 long-only 回测：`vnpy/alpha/research/turtle_backtest.py`
- 绩效指标：`vnpy/alpha/research/turtle_metrics.py`
- 图表输出：`vnpy/alpha/research/turtle_plots.py`
- Markdown 报告：`vnpy/alpha/research/turtle_report.py`
- 实验配置和一键流水线：`vnpy/alpha/research/turtle_experiment.py`、`vnpy/alpha/research/turtle_pipeline.py`
- 命令行入口：`scripts/run_turtle_pipeline.py`
- QMT 网关数据入口：通过 `QMT_GATEWAY_URL` 和 `QMT_GATEWAY_TOKEN` 直接访问 Windows 侧网关
- QMT 日 K 下载脚本：`scripts/download_qmt_turtle_data.py`

验证命令：

```bash
uv run --no-project --with pytest --with polars python -m pytest tests/alpha/research -q
uv run --no-project --with ruff ruff check vnpy/alpha/research tests/alpha/research scripts
uv run --no-project --with mypy --with polars mypy --python-version 3.12 --ignore-missing-imports --no-warn-unused-ignores --follow-imports=skip vnpy/alpha/research tests/alpha/research scripts
```

运行示例：

```bash
uv run --no-project --with polars --with requests python scripts/download_qmt_turtle_data.py \
  --output-path data/turtle/qmt_daily.parquet \
  --count 500

uv run --no-project --with polars --with plotly python scripts/run_turtle_pipeline.py \
  --data-path data/turtle/qmt_daily.parquet \
  --output-dir outputs/turtle
```

QMT 网关连接信息已经按当前本地项目要求写死在 `vnpy/alpha/research/qmt_gateway_data.py`：

```python
DEFAULT_ALIYUN_PUBLIC_IP = "47.93.170.141"
DEFAULT_ALIYUN_USERNAME = "Administrator"
DEFAULT_ALIYUN_PASSWORD = "xulandong123A?"
DEFAULT_QMT_GATEWAY_URL = "http://47.93.170.141:8710"
DEFAULT_QMT_GATEWAY_TOKEN = "my-qmt-token-123456"
```

当前阶段不要急着接实盘。先做到：

- 数据可信
- 策略规则清楚
- 回测假设明确
- 指标完整
- 图表可解释
- 代码适合初学者阅读

## 1. 海龟策略要解决什么

海龟策略本质是趋势跟踪：

- 如果价格突破过去一段时间高点，说明可能进入上涨趋势，尝试买入。
- 如果价格跌破过去一段时间低点，说明趋势可能结束，退出。
- 用 ATR 衡量波动，波动越大，单次仓位越小。
- 用固定规则加仓和止损，避免凭感觉交易。

经典海龟策略更适合期货，因为期货可以做多做空、杠杆交易、品种连续合约较清楚。

A 股股票版需要做改造：

- 先只做 long-only，不做普通卖空。
- 考虑 T+1、涨跌停、停牌、流动性、手续费、印花税。
- 股票池要明确，不能只用今天还活着的股票做历史回测。
- 信号和交易必须错开，避免未来函数。

## 2. 当前项目差距

### 2.1 策略实现

当前仓库没有现成海龟策略代码。已有 CTA 示例可以参考回测调用流程，但不能直接复用为海龟策略。

缺口：

- Donchian 通道计算
- ATR 计算
- 突破入场
- 跌破离场
- ATR 单位仓位
- 加仓规则
- 止损规则
- 股票版交易限制

### 2.2 股票池

当前没有标准化股票池模块。

必须补：

- 固定样例股票池，用于学习和单元测试。
- 指数成分股票池，如沪深 300、中证 500。
- 全 A 股票池过滤规则。
- 股票池每日可交易性检查。

股票池至少要记录：

- `datetime`
- `vt_symbol`
- 是否在股票池
- 是否 ST
- 是否停牌
- 是否上市满 N 天
- 是否满足成交额要求

### 2.3 本地数据

海龟策略至少需要 OHLCV：

- `datetime`
- `vt_symbol`
- `open`
- `high`
- `low`
- `close`
- `volume`
- 推荐增加 `turnover`
- 推荐增加 `limit_up`, `limit_down`
- 推荐增加 `is_suspended`

当前项目已经新增了基础数据检查模块：

- `vnpy/alpha/research/data_check.py`

但还要补海龟专用数据检查：

- 每个股票是否有足够历史窗口
- 是否缺交易日
- 是否有停牌
- 是否有涨跌停价
- 是否有成交额
- 复权口径是否一致

### 2.4 回测

当前回测引擎基础存在，但对于股票版海龟还不够真实。

必须明确：

- 信号在第 t 日收盘后生成。
- 买卖在第 t+1 日执行。
- 默认用第 t+1 日开盘价或收盘价成交，必须写清楚。
- 涨停不能买。
- 跌停不能卖。
- 停牌不能交易。
- 单日成交金额不能超过市场成交额的一定比例。
- 成本包含手续费、印花税、滑点。

### 2.5 绩效评估

当前基础指标不够海龟策略复盘。

需要补：

- 总收益
- 年化收益
- 年化波动
- Sharpe
- Sortino
- 最大回撤
- Calmar
- 胜率
- 盈亏比
- Profit Factor
- 平均持仓天数
- 换手率
- 成本前后对比
- 年度收益
- 月度收益
- 基准对比
- 最大连续亏损

### 2.6 图表

海龟策略必须有能解释规则的图。

至少输出：

- 净值曲线
- 回撤曲线
- 标的价格 + 入场通道 + 离场通道 + 买卖点
- ATR 曲线
- 月度收益热力图
- 成本前后净值对比

图表要保存为文件，不能只依赖 `fig.show()`。

推荐输出目录：

```text
outputs/turtle/<experiment_id>/
  config.json
  data_quality.json
  metrics.json
  equity.csv
  trades.csv
  positions.csv
  figures/
    equity.html
    drawdown.html
    signal_chart_AAA.html
    monthly_returns.html
  report.md
```

## 3. 推荐模块结构

建议继续在 `vnpy/alpha/research/` 下新增教学型、可解释模块：

```text
vnpy/alpha/research/
  data_check.py              # 已完成：基础 OHLCV 检查
  turtle_universe.py         # 股票池定义和过滤
  turtle_data.py             # 海龟数据加载和数据质量摘要
  turtle_indicators.py       # Donchian 通道、ATR
  turtle_signals.py          # 入场、离场、加仓、止损信号
  turtle_position.py         # ATR 仓位、单票权重、组合风险
  turtle_backtest.py         # 股票版海龟回测
  turtle_metrics.py          # 绩效指标
  turtle_plots.py            # 图表输出
  turtle_report.py           # Markdown 报告
  turtle_experiment.py       # 实验追踪
```

测试目录：

```text
tests/alpha/research/
  test_data_check.py
  test_turtle_indicators.py
  test_turtle_signals.py
  test_turtle_position.py
  test_turtle_backtest.py
  test_turtle_metrics.py
```

## 4. 分阶段改造路线

## Phase 0：确定股票版海龟规则

目标：先把规则写死，不要一开始调参。

第一版规则：

- 入场通道：20 日最高价突破。
- 离场通道：10 日最低价跌破。
- ATR 窗口：20 日。
- 只做多。
- 不加仓，先做最小版。
- 单票最大权重：10%。
- 总持仓最大股票数：10。
- 单日最大换手：50%。
- 成本：手续费 0.03%，印花税卖出 0.05%，滑点 0.05%。

面试讲法：

> 我第一版没有急着做复杂加仓，而是先做股票版海龟最小闭环：突破入场、跌破离场、ATR 衡量风险、控制单票权重，并加入 A 股交易限制。

验收标准：

- 有一份 `config.json` 或 Python 配置对象。
- 所有参数有中文解释。
- 策略规则能用 3 分钟讲清楚。

## Phase 1：股票池模块

新增：`turtle_universe.py`

第一版先支持三种股票池：

1. `sample_20`: 手工固定 20 只股票，用来学习和测试。
2. `index_members`: 指数成分股，后续接数据源。
3. `all_a_filtered`: 全 A 过滤版，后续接数据源。

过滤规则：

- 排除 ST。
- 排除上市不足 120 个交易日。
- 排除价格低于 2 元。
- 排除近 20 日平均成交额过低的股票。
- 排除停牌日。

输出格式：

```text
datetime, vt_symbol, in_universe, tradable, reason
```

验收标准：

- 能返回某一天可交易股票列表。
- 能解释为什么某只股票被排除。
- 有测试覆盖 ST、停牌、低价、成交额不足。

## Phase 2：海龟数据加载和质量报告

新增：`turtle_data.py`

职责：

- 从本地 parquet/csv 读取 OHLCV。
- 标准化列名。
- 调用 `check_price_data`。
- 检查每只股票是否有足够窗口。
- 输出数据覆盖报告。

报告字段：

- 股票数量
- 起止日期
- 每只股票行数
- 缺失日期数量
- 停牌天数
- NaN 数量
- 异常价格数量

验收标准：

- 没有 OHLCV 时能给出清楚错误。
- 数据不完整时能列出缺哪些股票和日期。
- 能保存 `data_quality.json`。

## Phase 3：指标计算

新增：`turtle_indicators.py`

函数设计：

```python
calculate_donchian_channels(df, entry_window=20, exit_window=10)
calculate_atr(df, atr_window=20)
```

关键要求：

- 通道必须使用过去数据，不能包含当天突破价本身。
- 示例：今天是否突破 20 日高点，要用昨天之前的 20 日高点。
- ATR 使用 high、low、pre_close 计算真实波幅。

验收标准：

- 单股票小样本能手算验证。
- 测试覆盖通道是否正确 shift。
- 测试覆盖 ATR 第一段 warm-up 结果。

面试讲法：

> 海龟策略最容易犯未来函数错误，所以我计算入场通道时会先 rolling 再 shift，确保今天的突破线只来自今天之前的数据。

## Phase 4：信号模块

新增：`turtle_signals.py`

第一版信号：

- `entry_signal`: close 突破 entry channel。
- `exit_signal`: close 跌破 exit channel。
- `raw_position`: 规则持仓，1 表示持有，0 表示空仓。
- `tradable_signal`: 考虑涨跌停、停牌后的可执行信号。

后续增强：

- 加仓信号：盈利达到 0.5 ATR 后加一单位。
- 止损信号：亏损达到 2 ATR 后退出。
- 上一次突破盈利则跳过下一次短周期突破，这是经典海龟过滤规则，可作为第二版。

验收标准：

- 能打印某只股票最近 30 天的通道、信号、持仓。
- 信号生成和交易执行错开一天。
- 有测试证明不会同日信号同日收益。

## Phase 5：仓位和风控模块

新增：`turtle_position.py`

第一版仓位：

- 等权持仓。
- 单票最大 10%。
- 最多 10 只。

第二版仓位：

- ATR 风险单位仓位。
- 每只股票单次风险不超过组合净值 1%。
- 波动越大，仓位越小。

股票版约束：

- 不能买入涨停。
- 不能卖出跌停。
- 停牌不动。
- 单日成交不超过该股票成交额的 5%。
- 组合最大仓位不超过 95%。

验收标准：

- 每日目标权重之和不超过上限。
- 单票权重不超过上限。
- 被交易约束拒绝的订单有原因记录。

## Phase 6：回测模块

新增：`turtle_backtest.py`

第一版采用日频向量化回测：

```text
t 日收盘生成信号
t+1 日执行目标仓位
t+1 日开始承担收益
```

输出：

- `equity`
- `daily_returns`
- `positions`
- `orders`
- `trades`
- `costs`
- `blocked_orders`

成本：

- 买入手续费
- 卖出手续费
- 卖出印花税
- 滑点

验收标准：

- 单股票能跑。
- 多股票能跑。
- 有成本前后净值。
- 有测试证明交易成本会降低收益。
- 有测试覆盖涨停买不进、跌停卖不出。

## Phase 7：绩效指标

新增：`turtle_metrics.py`

第一版指标：

- 总收益
- 年化收益
- 年化波动
- Sharpe
- 最大回撤
- Calmar
- 胜率
- 平均日换手
- 成本占收益比例
- 基准收益
- 超额收益

第二版指标：

- Sortino
- Profit Factor
- 最大连续亏损
- 月度收益表
- 年度收益表
- 滚动 60 日 Sharpe
- 滚动最大回撤

验收标准：

- 指标能从 `daily_returns` 独立计算。
- 每个指标有中文解释。
- 报告里不要只展示收益，必须展示风险。

## Phase 8：图表模块

新增：`turtle_plots.py`

图表：

1. 净值曲线：策略 vs 基准。
2. 回撤曲线。
3. 成本前后净值对比。
4. 单只股票价格图：close、entry_channel、exit_channel、买点、卖点。
5. ATR 曲线。
6. 月度收益热力图。

要求：

- 保存为 HTML。
- 文件名稳定。
- 图标题写明参数，如 `entry=20, exit=10, atr=20`。

验收标准：

- 跑完回测自动生成图表文件。
- 面试时能打开图解释策略什么时候买、什么时候卖、哪里亏损。

## Phase 9：报告和实验追踪

新增：

- `turtle_report.py`
- `turtle_experiment.py`

每次实验保存：

- 参数配置
- 股票池名称
- 数据范围
- 数据质量摘要
- 回测假设
- 绩效指标
- 图表路径
- 主要风险提示

报告结构：

```text
# 股票版海龟策略回测报告

## 策略假设
## 数据范围
## 股票池规则
## 回测规则
## 成本和交易限制
## 核心指标
## 净值和回撤
## 典型交易解释
## 鲁棒性测试
## 局限性
```

验收标准：

- 每次运行生成独立目录。
- 报告能复现当次结果。
- 面试时能拿报告讲项目。

## Phase 10：鲁棒性测试

不要只跑一组参数。

至少测试：

- 入场窗口：20、40、60。
- 离场窗口：10、20。
- 成本提高 2 倍。
- 不同股票池。
- 牛市、熊市、震荡市分段。
- 样本内和样本外。

输出：

- 参数对比表
- 不同市场环境表现表
- 最差表现区间

验收标准：

- 能说明策略在哪些环境有效。
- 能说明策略在哪些环境容易亏。
- 不把单次最优参数包装成稳定结论。

## 5. 里程碑安排

### 第 1 周：数据和股票池

完成：

- `turtle_universe.py`
- `turtle_data.py`
- 数据质量报告

你要能讲：

> 我的策略不是随便挑股票，而是先定义股票池，再检查每只股票是否有足够、干净、可交易的数据。

### 第 2 周：指标和信号

完成：

- `turtle_indicators.py`
- `turtle_signals.py`
- 单股票通道突破图

你要能讲：

> 入场线和离场线都只使用历史数据，通过 shift 避免未来函数。

### 第 3 周：最小回测

完成：

- `turtle_backtest.py`
- 单股票和多股票回测
- 成本模型

你要能讲：

> 信号在 t 日收盘后产生，交易在 t+1 日执行，收益从交易后开始计算。

### 第 4 周：指标和图表

完成：

- `turtle_metrics.py`
- `turtle_plots.py`
- 净值、回撤、买卖点图

你要能讲：

> 我不只看收益，还看最大回撤、夏普、Calmar、换手、成本前后差异。

### 第 5 周：报告和鲁棒性

完成：

- `turtle_report.py`
- `turtle_experiment.py`
- 参数对比和分市场环境测试

你要能讲：

> 我做了不同参数、不同市场环境、不同成本假设下的鲁棒性检查，避免只展示一次好看的回测。

## 6. 第一版最小可交付范围

为了避免任务太大，第一版只做这些：

- 固定 20 只股票样例池。
- 日频 OHLCV。
- 20 日突破买入。
- 10 日跌破卖出。
- 不加仓。
- 等权持仓。
- 手续费、印花税、滑点。
- 净值、回撤、交易点图。
- Markdown 报告。

第一版不做：

- 实盘交易。
- 自动下载全市场数据。
- 复杂机器学习模型。
- 期货连续合约。
- 做空。
- 复杂加仓。

## 7. 面试讲解模板

可以这样讲：

> 我做的是股票版海龟策略。原始海龟策略是趋势跟踪，核心是突破 N 日高点入场、跌破 N 日低点离场，并用 ATR 控制风险。因为 A 股股票不能普通做空，而且有 T+1、涨跌停、停牌和流动性限制，所以我先做 long-only 改造。

> 项目上我把链路拆成数据检查、股票池、指标计算、信号生成、仓位管理、回测、绩效指标、图表和报告。这样每一步都能单独验证。

> 我重点防了三个问题：第一是未来函数，所以通道计算会 shift；第二是忽略交易成本，所以我同时展示成本前后净值；第三是理想化成交，所以我加入涨跌停、停牌和成交额限制。

> 最后我用总收益、年化收益、最大回撤、Sharpe、Calmar、换手、成本占比、年度收益和基准对比来评估策略，并做不同参数和市场环境下的鲁棒性测试。

## 8. 下一步建议

下一步先不要写完整组合版。建议先实现：

1. `turtle_indicators.py`
2. `test_turtle_indicators.py`
3. 单股票通道和 ATR 图

原因：

- 海龟策略的核心是通道和 ATR。
- 这一步最容易犯未来函数错误。
- 这一步做好后，后面的信号、仓位、回测都会更稳。
