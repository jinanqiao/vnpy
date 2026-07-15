# 013 规模 + 真换手率因子检验（T5）

## 目标

用 akshare `stock_zh_a_daily`（含 `outstanding_share` 和真 `turnover` 换手率）
补齐 QMT 网关缺失的市值/股本数据，检验两个新候选因子：
- `size` = 对数流通市值（近似）= `ln(outstanding_share × close_hfq)`
- `turnover_rate_20` = akshare 日换手率近 20 日均值

## 数据源（akshare）

- 脚本：`scripts/download_akshare_daily.py`
- 产物：`data/akshare/daily_bars_outstanding.parquet`
- 覆盖：5200 只 A 股（原池 5206 只，缺 6 只），2018-01-02 ~ 2026-07-09
- 8899630 行、6 列（datetime, vt_symbol, close_hfq, outstanding_share, turnover_rate, amount）
- 关键坑（写进 memory 前先记录这里）：
  1. akshare 内部用 `py_mini_racer`（V8 引擎）做 JS 解密，**多线程会 native crash**
     （libmini_racer + address_pool_manager 冲突），必须用 `ProcessPoolExecutor`；
  2. `pl.from_pandas()` 依赖 pyarrow，仓库 pyarrow 是 x86_64，arm64 python 载不了，
     必须手动 dict→polars 构造；
  3. Framework Python 需要 `arch -arm64` 前缀 + `SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')`。

## 检验结果（2026-07-09，与 011 同一模板）

样本区间：2021-03-05 ~ 2026-07-02（执行池覆盖范围），5 分层。

### weekly / all 池（272 期）

| 因子 | IC均值 | t值 | Q1 | Q3 | Q5 | 顶底价差年化 | 与 rs / nh / vol_ratio |
|---|---|---|---|---|---|---|---|
| size | -0.018 | -1.73 | +0.42% | +0.22% | +0.10% | -16.7% | +0.01 / +0.13 / +0.01 |
| turnover_rate_20 | -0.055 | **-4.15** | +0.21% | +0.33% | +0.02% | -9.8% | +0.12 / -0.10 / +0.28 |

### monthly / all 池（64 期）

| 因子 | IC均值 | t值 | Q1 | Q5 | 顶底价差年化 | 与 vol_ratio |
|---|---|---|---|---|---|---|
| size | -0.034 | -1.65 | +1.78% | +0.58% | -14.4% | +0.02 |
| turnover_rate_20 | -0.072 | **-2.76** | +0.88% | +0.73% | -1.9% | +0.27 |

## 闸门评估

### size（对数流通市值）— **未通过** ✗

- 月频 |t|=1.65（< 2 阈值），周频 |t|=1.73（也 < 2）
- 方向对（负 IC = 小盘赢），但**样本内显著性不足**
- 与生产三因子相关都 < 0.13（独立性满足），是个"独立但弱"的候选
- 顶底价差 -14 ~ -17%/年，Q3 分层已经不单调
- **结论**：不接入。样本内 Alpha 不够稳，加上"小盘规模溢价 = 011 教训"的先验，
  不值得再花容量鲁棒性对照的功夫

### turnover_rate_20 — **过 t 值闸门，过独立性闸门，但过不了 011 教训** ✗

- 月频 t=-2.76，周频 t=-4.15（都通过 |t|>=2）
- 与 rs/nh/vol 相关 |ρ| ≤ 0.28（都 < 0.5 独立性阈值）
- **但**：与 vol_ratio 相关 +0.27~0.28，与 Amihud 是同一类"流动性代理"信号
  （011 Amihud 就是"看着漂亮但接入后是小盘溢价"），大概率重演
- 顶底价差年化只有 -1.9%（月频）/ -9.8%（周频），Q3 反弹（单调"否"）
- **结论**：不直接接入。若要进一步测，参考 011 走 T2b 三档权重 + 容量鲁棒性
  对照才能证伪；但基于 011 先验，投产收益预期 ≤ 011（011 都被容量证伪了）

## 综合结论

**两个新因子都不接入生产**。

- size 样本内不显著，直接放弃
- turnover_rate_20 与 011 的 Amihud 是"同源"流动性代理，011 已充分证伪，
  基于 memory `feedback-factor-gate-needs-capacity-check` 的先验，不再重复走 T2b 流程
- 生产维持：`score_weights=(-0.40, +0.35, -0.25)`（008 版），无第四/第五因子
- akshare 数据保留在 `data/akshare/`，未来若引入财务/质量类因子仍可用

## 后续（handoff）

- **T5 数据部分已完成**（5200 只 x 8.5 年入湖），后续 T6（财务 + PIT）可以直接
  基于同一套 akshare 通道扩展（`stock_financial_report_sina` 有公告日期）；
- 目前仓库里"独立于价量的信号"仍是空白——T6 财务因子（ROE / 净利润增长率 / SUE）
  是唯一还没试过的方向，也是**唯一有理论理由不与小盘规模挂钩**的候选，
  handoff 里排最后（"最大不确定项"）的位置合理。

## 复现命令

```bash
# 1) 下载 akshare 数据（一次性 ~30 分钟）
arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    scripts/download_akshare_daily.py --workers 6 --start 20180101

# 2) 跑因子检验
arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    scripts/validate_size_liquidity_factors.py
```

产物：`outputs/factor_checks/size_liquidity_gics1.md`
