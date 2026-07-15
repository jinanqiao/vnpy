"""回测的全部参数。

这个文件回答一个问题：回测有哪些可以调的"旋钮"，默认值是多少。
全部参数集中在 BacktestConfig 一个类里（整个子包唯一的类），
运行时的参数快照会原样写进产物目录的 config.json，保证每次结果可追溯。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class BacktestConfig:
    """回测参数集合（frozen=True 表示创建后不可修改，防止运行途中被偷偷改动）。"""

    # ---- 信号来源（001 特性的产物，回测唯一的调仓依据） ----
    selection_path: str = ""

    # ---- 数据文件路径（相对 data_dir） ----
    data_dir: str = "data"
    adjusted_bars_file: str = "silver/daily_bars_adjusted.parquet"             # 后复权（算收益）
    unadjusted_bars_file: str = "silver/daily_bars_raw_price.parquet"          # 未复权（算整手股数）
    benchmark_file: str = "silver/benchmark_index_daily.parquet"
    benchmark_symbol: str = "000300.SH"    # 沪深300
    execution_universe_file: str = "gold/execution_universe.parquet"
    trading_dates_file: str = "silver/trading_calendar.parquet"

    # ---- 资金与交易约束 ----
    initial_capital: float = 1_000_000.0   # 初始资金（元）
    lot_size: int = 100                    # A 股整手：买入股数必须是 100 的整数倍

    # ---- 成本模型（任一项可置 0 做对照实验） ----
    commission_rate: float = 0.00025       # 佣金：买卖双边各收 0.025%
    stamp_tax_rate: float = 0.0005         # 印花税：只在卖出时收 0.05%
    slippage_rate: float = 0.001           # 滑点：买入价上浮 0.1%、卖出价下压 0.1%

    # ---- 特殊情况处理 ----
    suspend_freeze_days: int = 20          # 停牌超过 20 个交易日的持仓在报告中单列

    # ---- 差量调仓（009）：调仓日只交易名单差集 ----
    # 关（默认）= 002 基线语义：全卖旧仓再全买新仓，新旧重叠的股票会被卖了再买回，
    # 白付一轮双边成本；开 = 出局的卖、新进的买、连任的原仓保留不动（不再修剪权重，
    # 等权漂移留到该股出局时自然归零——反转策略连任本来就少，漂移可忽略）。
    delta_rebalance_enabled: bool = False

    # ---- 风控层（003 特性，默认全关 = 002 基线行为） ----
    timing_enabled: bool = False           # 大盘择时：执行日前一日基准跌破均线则清仓空仓
    timing_ma_window: int = 60             # 择时均线窗口（交易日数）
    # 逐日择时（007）：不等调仓日，任意交易日只要前一日基准跌破均线就清仓。
    # 实证背景：只在调仓日检查会留下 23% 的弱市持仓暴露（risk_rules_check.md）。
    timing_daily_enabled: bool = False     # 需要 timing_enabled=True 才能打开
    # 逐日再入场（008 追加）：择时清仓/弱市跳过建仓后，不等下个调仓日，
    # 基准收盘回到均线上方的次一交易日就恢复买入当期清单。
    # 动机：逐日清仓 + 月末才回场是不对称的，短均线被假摔击穿后要空仓最多
    # 一个月，牛市里踏空成本远大于躲跌收益（见 008 spec 的 MA60 对照）。
    timing_reentry_enabled: bool = False   # 需要 timing_daily_enabled=True 才能打开
    no_signal_exit_enabled: bool = False   # 无信号月清仓：信号层空月时卖光旧仓
    stop_loss_enabled: bool = False        # 个股止损：较买入执行价回撤超阈值次日卖出
    stop_loss_rate: float = 0.20           # 止损阈值（回撤比例，必须 > 0；008 后用户指定 20%）

    # ---- 单股最大权重上限（S15，默认 0 = 关闭 = 002 基线行为） ----
    # 0 = 关（等权 equity/N 分配，与旧行为逐字节一致）；
    # 0 < x <= 1 = 单股目标金额上限为 x * equity，多出来的现金保留（不强行往其他股票堆）。
    # 动机：小组合规模时避免单股风险敞口过大。例如 30 只等权理论 3.3%，
    # 但一旦其它股票遇到跌停/停牌只能买到少数几只时，基线会把过多现金堆到一只上。
    max_position_weight: float = 0.0

    # ---- 组合波动率目标（S7，默认关闭 = 002 基线路径零改动） ----
    # 打开后调仓时按最近若干天组合日收益标准差估计当前年化波动率 σ，
    # 目标金额缩放系数 = clip(target/σ, min, max)。
    # 意图：市场平静（σ 低）时加仓、剧烈（σ 高）时降仓；min/max 防止极端。
    # 首个调仓期或历史 nav 不足 5 天时缩放系数强制 = 1.0（无先验数据）。
    # 实证（specs/017-portfolio-vol-target/）：target=0.10 样本内年化 15.5% → 18.4%，
    # Sharpe 0.98 → 1.31，回撤 -19.2% → -13.1%（三重改善）。
    portfolio_vol_target: float = 0.0      # 年化目标波动率，0 = 关闭（如 0.10 = 10%）
    portfolio_vol_window: int = 20         # 采样窗口（用近 N 日 nav 变化算 σ）
    portfolio_vol_scale_min: float = 0.5   # 缩放系数下限
    portfolio_vol_scale_max: float = 1.5   # 缩放系数上限

    # ---- 运行参数 ----
    start: str = ""                        # 只回测该日期之后的调仓期（空 = 不限制）
    end: str = ""                          # 只回测该日期之前的调仓期（空 = 不限制）
    output_dir: str = "outputs/mainline_backtest"
    name: str = "mainline_backtest"

    def __post_init__(self) -> None:
        """构建时校验风控参数，非法值直接报错而不是静默跑出错误结果。"""
        if self.stop_loss_rate <= 0:
            raise ValueError(f"stop_loss_rate 必须 > 0，当前 {self.stop_loss_rate}")
        if self.timing_ma_window < 2:
            raise ValueError(f"timing_ma_window 必须 >= 2，当前 {self.timing_ma_window}")
        if self.timing_daily_enabled and not self.timing_enabled:
            raise ValueError("timing_daily_enabled 需要 timing_enabled=True（逐日择时是择时的加强版）")
        if self.timing_reentry_enabled and not self.timing_daily_enabled:
            raise ValueError("timing_reentry_enabled 需要 timing_daily_enabled=True（逐日再入场与逐日清仓配对使用）")
        if not (0.0 <= self.max_position_weight <= 1.0):
            raise ValueError(f"max_position_weight 必须落在 [0, 1] 内（0 = 关闭），当前 {self.max_position_weight}")
        # 波动率目标校验（0 = 关闭，允许；> 0 时必须自洽）
        if self.portfolio_vol_target < 0:
            raise ValueError(f"portfolio_vol_target 必须 >= 0（0 表示关闭），当前 {self.portfolio_vol_target}")
        if self.portfolio_vol_scale_min <= 0 or self.portfolio_vol_scale_max <= 0:
            raise ValueError(f"portfolio_vol_scale_min/max 必须 > 0，"
                             f"当前 min={self.portfolio_vol_scale_min}, max={self.portfolio_vol_scale_max}")
        if self.portfolio_vol_scale_min >= self.portfolio_vol_scale_max:
            raise ValueError(f"portfolio_vol_scale_min 必须 < portfolio_vol_scale_max，"
                             f"当前 min={self.portfolio_vol_scale_min} vs max={self.portfolio_vol_scale_max}")
        if self.portfolio_vol_target > 0 and self.portfolio_vol_window < 2:
            raise ValueError(f"portfolio_vol_window 必须 >= 2，当前 {self.portfolio_vol_window}")

    def to_dict(self) -> dict:
        """把全部参数转成普通字典，用于写 config.json 快照。"""
        return asdict(self)


def load_config_from_json(path: str | Path, base: BacktestConfig | None = None) -> BacktestConfig:
    """从 JSON 文件读取参数覆盖，返回新的 BacktestConfig。

    JSON 里只需要写想改的字段，例如 {"slippage_rate": 0.003}；
    没写的字段保持 base（默认配置）的值。字段名写错会直接报错，防止静默失效。
    """
    base = base or BacktestConfig()
    overrides: dict = json.loads(Path(path).read_text(encoding="utf-8"))

    valid_fields = set(base.to_dict().keys())
    unknown = sorted(set(overrides.keys()) - valid_fields)
    if unknown:
        raise ValueError(f"配置文件里有不认识的字段: {unknown}，请检查拼写")

    return replace(base, **overrides)


def zero_cost(config: BacktestConfig) -> BacktestConfig:
    """返回三项成本全部归零的配置副本（对照实验用）。"""
    return replace(config, commission_rate=0.0, stamp_tax_rate=0.0, slippage_rate=0.0)
