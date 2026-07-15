"""主线强势股策略的全部参数。

这个文件回答一个问题：策略有哪些可以调的"旋钮"，默认值是多少。
全部参数集中在 MainlineConfig 一个类里（整个子包唯一的类），
运行时的参数快照会原样写进产物目录的 config.json，保证每次结果可追溯。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path


@dataclass(frozen=True)
class MainlineConfig:
    """策略参数集合（frozen=True 表示创建后不可修改，防止运行途中被偷偷改动）。"""

    # ---- 数据文件路径（相对 data_dir） ----
    data_dir: str = "data"
    daily_bars_file: str = "silver/daily_bars_raw_price.parquet"
    sector_members_file: str = "silver/sector_members_snapshot.parquet"
    sw1_members_file: str = "silver/sw1_members_snapshot.parquet"
    execution_universe_file: str = "gold/execution_universe.parquet"
    trading_dates_file: str = "silver/trading_calendar.parquet"
    hs300_members_file: str = "universe/hs300_members.parquet"
    zz500_members_file: str = "universe/zz500_members.parquet"

    # ---- 股票池：all_a = 全部 A 股；leaders = 沪深300 + 中证500 成分股（龙头池） ----
    universe: str = "all_a"

    # ---- 行业口径：gics1 = GICS 一级（11 个，粗）；sw1 = 申万一级（31 个，细） ----
    # 实证背景：行业动量的 Rank IC 检验（outputs/factor_checks/）显示两种口径
    # 在近 5 年的周/月持有期上都没有稳定预测力，换细口径救不了"选行业"。
    industry_source: str = "gics1"

    # ---- 行业层的角色（008 调整：默认关闭行业层）----
    # none   = 不要行业层（当前默认）：每个调仓日直接在全市场过滤打分选股，
    #          相对强度的基准是全市场等权动量；
    # select = 先选主线行业、再在行业内选股（旧默认，保留做对照）；
    # regime = 行业层只当"有没有主线"的市场状态开关，有主线才全市场选股。
    industry_mode: str = "none"
    regime_top_n: int = 30        # none/regime 模式下全市场最多选多少只（对齐 3 行业 x 10 只）
    # 010 行业上限：none/regime 模式下每个行业最多贡献几只入选个股。
    # 0 = 不限制（默认，保持 008 后行为，逐字节兼容）；>0 时先在各行业内按 score 排前
    # max_per_industry 只作为候选池，再从候选池全局按 score 取前 regime_top_n。
    # 实证背景：008 版无行业层导致某些期最大行业占比 19/30，行业风险敞口过大。
    max_per_industry: int = 0

    # ---- 行业层参数 ----
    mom_window: int = 20          # 行业动量主窗口：过去 20 个交易日涨幅（原 60，改短以更快捕捉新主线）
    confirm_window: int = 5       # 行业动量确认窗口：过去 5 个交易日（原 20），防止追到已衰竭的行业
    breadth_window: int = 20      # 上涨广度窗口：行业内多少比例的股票在近 20 日上涨（原 60）
    breadth_min: float = 0.60     # 广度下限：低于 60% 视为权重股独涨的"伪主线"
    industry_rank_gate: int = 5   # 动量排名门槛：双窗口排名都要进前 5 才是候选
    industry_top_n: int = 3       # 最多选几个主线行业
    min_industry_members: int = 5  # 行业成分股少于 5 只时不参与排名

    # ---- 个股过滤参数 ----
    # 008 简化：趋势三关（均线多头/乖离/距新高）默认关闭——池级检验显示
    # 过滤后的池子整体跑输全市场（outputs/factor_checks/stock_factors_gics1.md）。
    # 关闭后只保留两项必要过滤：上市满一年 + 当日可交易。
    trend_filters_enabled: bool = False
    ma_windows: tuple[int, int, int] = (20, 60, 120)  # 均线多头排列：MA20 > MA60 > MA120
    ma_slope_lag: int = 5         # MA20 还要高于 5 个交易日前的自己（均线向上）
    bias_max: float = 0.25        # 乖离率上限：收盘价高出 MA20 超过 25% 视为过热
    min_listed_bars: int = 252    # 上市天数门槛：保证 52 周新高等窗口能完整计算
    nh_window: int = 252          # 52 周新高窗口（一年约 252 个交易日）
    nh_min: float = 0.80          # 距新高下限：收盘价低于一年高点的 80% 视为趋势破坏

    # ---- 个股打分参数 ----
    vol_short: int = 20           # 量价因子短窗口：近 20 日日均成交额
    vol_long: int = 60            # 量价因子长窗口：近 60 日日均成交额
    vol_cap: float = 3.0          # 量比上限截断：防止把游资爆炒股排到最前
    # 008 调整：相对强度与量价反向使用（负权重 = 排名越低分越高），距新高保持正向。
    # 实证背景：rs/vol 的 Rank IC 在 2021-2025 显著为负（短期反转效应），nh 是唯一为正的因子；
    # 注意 2026 年因子方向已翻正，本组权重属样本内结论，需持续跟踪。
    score_weights: tuple[float, float, float] = (-0.40, 0.35, -0.25)  # 相对强度 / 距新高 / 量价
    # 009 可选第四因子：低波动率（近 volatility_window 日日收益标准差）。
    # 实证：月频 Rank IC -0.085（t=-3.6），高波组显著跑输低波组，且与动量类因子
    # 截面相关仅 +0.26（正交性好，是打分里唯一不姓"动量"的腿）。
    # 权重为负 = 波动越低分越高；0 = 关闭（默认，保持 008 行为）。
    volatility_window: int = 20
    volatility_weight: float = 0.0
    # 011 可选第五因子：Amihud 非流动性（近 illiq_window 日 mean(|日收益|/成交额)）。
    # 实证（outputs/factor_checks/lottery_liquidity_gics1.md）：
    #   周频 IC +0.046 (t=+4.34)、月频 IC +0.077 (t=+3.44)，Q1→Q5 分层单调升；
    #   与现有 rs/nh/vol 三因子截面相关绝对值 ≤ 0.09（真独立信息，不与反转类同源）。
    # 权重为正 = 越不流动分越高（正向使用）；0 = 关闭（默认，保持 008/009 行为）。
    illiq_window: int = 20
    illiq_weight: float = 0.0
    # 011 容量鲁棒性开关：调仓日近 vol_short 天均成交额（元）低于该阈值直接剔除。
    # 与 illiq_weight 配合使用：Amihud 把选股拉向小盘，硬过滤按住底部一批。
    # 0 = 不启用（默认，与 010/T2b 逐字节兼容）。
    # 参考量级：2022-06 全市场 turnover_avg20 中位数约 1e8，p20 约 4e7；
    # 2024-06 中位数约 6e7，p20 约 2.6e7（一亿=1e8）。
    min_turnover_avg20: float = 0.0
    # 012 低价 / ST 硬过滤（避开壳股 / 低质小盘，011 教训：Amihud 把选股推向低价小盘）。
    # min_price：调仓日收盘价（元）低于该阈值直接剔除。0 = 不启用（默认，逐字节回归）。
    # exclude_st：是否剔除 ST。当前实现不做实际过滤（仓库里无股票名快照），
    # 保留字段供未来接入 akshare 股票名后落地；启用时 pipeline 会打印警告告知未生效。
    min_price: float = 0.0
    exclude_st: bool = False
    # S16 剔除高波股（"过热"过滤）：近 volatility_window 日日收益 std > max_volatility 直接剔除。
    # 0 = 不启用（默认，逐字节回归旧行为）；正数上限（如 0.05 = 5%）视为过热剔除。
    # 目的：避开彻底炒作的妖股；只做硬过滤上限、不进入打分排名，
    # 不会把候选池整体推向"越小盘越好"的规模溢价代理（吸取 011 Amihud 教训）。
    max_volatility: float = 0.0

    # ---- S13 组合内相关性去重（默认关闭，逐字节回归） ----
    # 逐个调仓日选完 top N 之后，逐个检查两两间近 corr_window 日日收益的皮尔逊相关系数，
    # 超过阈值的剔除排名靠后的那只，避免 30 只全扎堆在同一细分主线。
    # 0 或 >=1 = 关闭；0 < x < 1 = 相关性上限（越小越严格）。
    # 实证：max_pair_corr=0.8 时 Sharpe 0.98→1.03、回撤 -19.2%→-17.7%、年化基本持平。
    max_pair_corr: float = 0.0
    corr_window: int = 60

    # ---- 组合构建参数 ----
    stocks_per_industry_min: int = 6   # 每个行业期望至少选 6 只（不足时全要并记录）
    stocks_per_industry_max: int = 10  # 每个行业最多选 10 只

    # ---- 主线新鲜度参数（004） ----
    # 主线行业连续入选月数的上限：0 = 不限制（现状）；1 = 只买"连续入选第 1 个月"的新晋主线。
    # 实证背景：主线行业连任到第 2 个月起，其个股的下期收益均值转负（吃趋势尾巴）。
    max_industry_streak: int = 0

    # ---- 数据质量参数 ----
    suspect_drop_threshold: float = -0.15  # 单日跌幅超过 15% 记为疑似除权异常（数据未复权）
    # 数据门禁模式：空字符串 = 不在研究入口强制门禁；live/paper/backtest/research = 信号生成前先跑 data gate。
    # 实盘配置必须显式设为 live，门禁失败时 pipeline 会直接中止，不写订单候选。
    data_gate_mode: str = ""

    # ---- 运行参数 ----
    rebalance_freq: str = "weekly"  # 调仓频率：weekly = 每周最后一个交易日；monthly = 每月最后一个交易日
    start: str = ""               # 只处理该日期之后的调仓日（空 = 不限制），格式 2022-03-01
    end: str = ""                 # 只处理该日期之前的调仓日（空 = 不限制）
    output_dir: str = "outputs/mainline"
    name: str = "mainline_signals"

    def __post_init__(self) -> None:
        """参数合法性检查：创建即报错，避免带着非法参数跑完全程才发现。"""
        if self.max_industry_streak < 0:
            raise ValueError(f"max_industry_streak 不能为负: {self.max_industry_streak}")
        if self.rebalance_freq not in ("weekly", "monthly"):
            raise ValueError(f"rebalance_freq 只支持 weekly / monthly，当前: {self.rebalance_freq}")
        if self.data_gate_mode not in ("", "research", "backtest", "paper", "live"):
            raise ValueError(f"data_gate_mode 只支持空/research/backtest/paper/live，当前: {self.data_gate_mode}")
        if self.industry_source not in ("gics1", "sw1"):
            raise ValueError(f"industry_source 只支持 gics1 / sw1，当前: {self.industry_source}")
        if self.industry_mode not in ("none", "select", "regime"):
            raise ValueError(f"industry_mode 只支持 none / select / regime，当前: {self.industry_mode}")
        if self.regime_top_n <= 0:
            raise ValueError(f"regime_top_n 必须为正: {self.regime_top_n}")
        if self.max_per_industry < 0:
            raise ValueError(f"max_per_industry 不能为负: {self.max_per_industry}")
        if self.illiq_window <= 0:
            raise ValueError(f"illiq_window 必须为正: {self.illiq_window}")
        if self.min_turnover_avg20 < 0:
            raise ValueError(f"min_turnover_avg20 不能为负: {self.min_turnover_avg20}")
        if self.min_price < 0:
            raise ValueError(f"min_price 不能为负: {self.min_price}")
        if self.max_volatility < 0:
            raise ValueError(f"max_volatility 不能为负: {self.max_volatility}")
        if not (0.0 <= self.max_pair_corr <= 1.0):
            raise ValueError(f"max_pair_corr 必须在 [0, 1] 之间: {self.max_pair_corr}")
        if self.corr_window <= 1:
            raise ValueError(f"corr_window 必须 >= 2: {self.corr_window}")

    def to_dict(self) -> dict:
        """把全部参数转成普通字典，用于写 config.json 快照。"""
        return asdict(self)


def load_config_from_json(path: str | Path, base: MainlineConfig | None = None) -> MainlineConfig:
    """从 JSON 文件读取参数覆盖，返回新的 MainlineConfig。

    JSON 里只需要写想改的字段，例如 {"industry_top_n": 5}；
    没写的字段保持 base（默认配置）的值。字段名写错会直接报错，防止静默失效。
    """
    base = base or MainlineConfig()
    text = Path(path).read_text(encoding="utf-8")
    overrides: dict = json.loads(text)

    valid_fields = set(base.to_dict().keys())
    unknown = sorted(set(overrides.keys()) - valid_fields)
    if unknown:
        raise ValueError(f"配置文件里有不认识的字段: {unknown}，请检查拼写")

    # JSON 的数组会读成 list，而 dataclass 里定义的是 tuple，这里统一转回 tuple
    for key, value in overrides.items():
        if isinstance(value, list):
            overrides[key] = tuple(value)

    return replace(base, **overrides)
