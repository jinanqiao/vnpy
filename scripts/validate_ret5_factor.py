"""ret_5 短反转因子检验（用户问：加进策略会不会好）。

ret_5 = 近 5 天累计收益 = close(t) / close(t-5) - 1
预期负 IC（跌得多的近 5 天股票下期反弹）
关键看：与 rs_60 的相关性有多高？如果 > 0.5 就是同源信号叠加，没必要。
"""
from __future__ import annotations

from pathlib import Path
import polars as pl
from scipy import stats

MIN_LISTED_BARS = 252
N_QUANTILES = 5
VOL_CAP = 3.0


def load_features(bars_path):
    bars = pl.read_parquet(
        bars_path, columns=["datetime", "vt_symbol", "close", "high", "turnover"]
    ).with_columns(pl.col("datetime").cast(pl.Date).alias("d")).sort(["vt_symbol", "d"])

    over = {"partition_by": "vt_symbol"}
    return bars.with_columns(
        (pl.col("close") / pl.col("close").shift(5).over(**over) - 1).alias("ret_5"),
        (pl.col("close") / pl.col("close").shift(20).over(**over) - 1).alias("ret_20"),
        (pl.col("close") / pl.col("close").shift(60).over(**over) - 1).alias("ret_60"),
        (pl.col("close") / pl.col("high").rolling_max(252).over(**over)).alias("nh_252"),
        (
            pl.col("turnover").rolling_mean(20).over(**over)
            / pl.col("turnover").rolling_mean(60).over(**over)
        ).clip(upper_bound=VOL_CAP).alias("vol_ratio"),
        pl.int_range(pl.len()).over("vt_symbol").alias("listed_bars"),
    )


def load_industry_mom(bars, members_path, prefix):
    members = (
        pl.read_parquet(members_path)
        .filter(pl.col("sector").str.starts_with(prefix))
        .select(pl.col("sector").alias("industry"), "vt_symbol")
        .unique()
    )
    daily = (
        bars.select("vt_symbol", "d", "close").sort(["vt_symbol", "d"])
        .with_columns((pl.col("close") / pl.col("close").shift(1).over("vt_symbol") - 1).alias("ret"))
        .drop_nulls("ret")
        .join(members, on="vt_symbol", how="inner")
        .group_by(["industry", "d"]).agg(pl.col("ret").mean().alias("ind_ret"))
        .sort(["industry", "d"])
        .with_columns((pl.col("ind_ret") + 1).cum_prod().over("industry").alias("idx"))
    )
    return daily.with_columns(
        (pl.col("idx") / pl.col("idx").shift(20).over("industry") - 1).alias("ind_mom_20"),
        (pl.col("idx") / pl.col("idx").shift(60).over("industry") - 1).alias("ind_mom_60"),
    ).select("industry", "d", "ind_mom_20", "ind_mom_60"), members


def build_period_ends(dates, freq):
    frame = pl.DataFrame({"d": dates}).sort("d")
    if freq == "weekly":
        key = pl.col("d").dt.iso_year().cast(pl.String) + "-W" + pl.col("d").dt.week().cast(pl.String)
    else:
        key = pl.col("d").dt.strftime("%Y-%m")
    return (frame.with_columns(key.alias("k")).group_by("k").agg(pl.col("d").max())
            .sort("d").get_column("d").to_list())[:-1]


def eval_ic(pool, factor, ppy):
    data = pool.select("d", factor, "fwd_ret").drop_nulls()
    ics = []
    for (_, g) in data.group_by("d"):
        if len(g) >= 30:
            ic, _ = stats.spearmanr(g[factor].to_list(), g["fwd_ret"].to_list())
            ics.append(ic)
    s = pl.Series(ics)
    n = len(s)
    m, sd = s.mean(), s.std()
    ranked = data.with_columns(
        ((pl.col(factor).rank("ordinal").over("d") - 1) * N_QUANTILES
         // pl.col(factor).count().over("d")).alias("q")
    )
    q_means = (ranked.group_by(["d", "q"]).agg(pl.col("fwd_ret").mean().alias("q_ret"))
               .group_by("q").agg(pl.col("q_ret").mean().alias("mean_ret")).sort("q")
               .get_column("mean_ret").to_list())
    top = ranked.filter(pl.col("q") == N_QUANTILES - 1).group_by("d").agg(pl.col("fwd_ret").mean().alias("top"))
    bot = ranked.filter(pl.col("q") == 0).group_by("d").agg(pl.col("fwd_ret").mean().alias("bot"))
    spread = top.join(bot, on="d").with_columns((pl.col("top") - pl.col("bot")).alias("sp")).get_column("sp")
    return {
        "ic": m, "t": m / sd * (n**0.5) if sd else float('nan'),
        "q_means": q_means, "spread_ann": spread.mean() * ppy,
        "spread_t": spread.mean() / spread.std() * (len(spread)**0.5) if spread.std() else float('nan'),
    }


def cross_corr(pool, a, b):
    data = pool.select("d", a, b).drop_nulls()
    corrs = []
    for (_, g) in data.group_by("d"):
        if len(g) >= 30:
            r, _ = stats.spearmanr(g[a].to_list(), g[b].to_list())
            corrs.append(r)
    return float(pl.Series(corrs).mean()) if corrs else float('nan')


def main():
    features = load_features(Path("data/silver/daily_bars_adjusted.parquet"))
    ind_mom, members = load_industry_mom(features, Path("data/sector/sector_members.parquet"), "GICS1")
    universe = (pl.read_parquet("data/universe/execution_universe.parquet",
                columns=["datetime", "vt_symbol", "in_execution"])
                .with_columns(pl.col("datetime").cast(pl.Date)))
    trade_dates = sorted(features.filter(
        (pl.col("d") >= universe.get_column("datetime").min())
        & (pl.col("d") <= universe.get_column("datetime").max())
    ).get_column("d").unique().to_list())

    lines = ["# ret_5 短反转因子检验", ""]

    for freq, rs_col, ppy in [("monthly", "rs_60", 12), ("weekly", "rs_20", 52)]:
        period_ends = build_period_ends(trade_dates, freq)
        order = {d: i for i, d in enumerate(period_ends)}
        snap = (features.filter(pl.col("d").is_in(period_ends))
                .join(members, on="vt_symbol", how="inner")
                .join(ind_mom, on=["industry", "d"], how="left")
                .join(universe, left_on=["d", "vt_symbol"], right_on=["datetime", "vt_symbol"], how="left")
                .with_columns(
                    pl.col("in_execution").fill_null(False),
                    (pl.col("ret_20") - pl.col("ind_mom_20")).alias("rs_20"),
                    (pl.col("ret_60") - pl.col("ind_mom_60")).alias("rs_60"),
                    pl.col("d").replace_strict(order, return_dtype=pl.Int64).alias("pidx"),
                )
                .sort(["vt_symbol", "pidx"])
                .with_columns(
                    pl.when(pl.col("pidx").shift(-1).over("vt_symbol") == pl.col("pidx") + 1)
                    .then(pl.col("close").shift(-1).over("vt_symbol") / pl.col("close") - 1)
                    .otherwise(None).alias("fwd_ret")
                ))
        pool = snap.filter(
            pl.col("fwd_ret").is_not_null()
            & pl.col("in_execution")
            & (pl.col("listed_bars") >= MIN_LISTED_BARS)
        )
        pool_size = pool.group_by("d").len().get_column("len").median() if pool.height else 0

        r = eval_ic(pool, "ret_5", ppy)
        q = r["q_means"]
        lines.append(f"## {freq}（{len(period_ends)} 期，池中位 {pool_size:.0f} 只）")
        lines.append("")
        lines.append("| 因子 | IC | t值 | Q1 | Q3 | Q5 | 顶底价差年化 | 价差t |")
        lines.append("|---|---|---|---|---|---|---|---|")
        lines.append(f"| ret_5 | {r['ic']:+.3f} | {r['t']:+.2f} | {q[0]*100:+.2f}% | {q[2]*100:+.2f}% | {q[-1]*100:+.2f}% | {r['spread_ann']*100:+.1f}% | {r['spread_t']:+.2f} |")

        print(f"[{freq}] ret_5: IC={r['ic']:+.4f}, t={r['t']:+.2f}, spread ann={r['spread_ann']*100:+.1f}%")

        # 与老三因子相关性
        c_rs = cross_corr(pool, "ret_5", rs_col)
        c_nh = cross_corr(pool, "ret_5", "nh_252")
        c_vol = cross_corr(pool, "ret_5", "vol_ratio")
        lines.append("")
        lines.append(f"**独立性**：与 {rs_col} = **{c_rs:+.2f}** | 与 nh_252 = {c_nh:+.2f} | 与 vol_ratio = {c_vol:+.2f}")
        lines.append("")
        print(f"  ↳ 相关：{rs_col}={c_rs:+.2f}  nh={c_nh:+.2f}  vol={c_vol:+.2f}")

    out = Path("outputs/factor_checks/ret5_factor.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 → {out}")


if __name__ == "__main__":
    main()
