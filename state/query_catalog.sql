-- 数据底座查询目录，供 DuckDB 或兼容工具执行。
-- 大表查询建议始终带 datetime / vt_symbol 过滤条件。

CREATE OR REPLACE VIEW daily_bars_raw_price AS
SELECT * FROM read_parquet('data/silver/daily_bars_raw_price.parquet');

CREATE OR REPLACE VIEW daily_bars_adjusted AS
SELECT * FROM read_parquet('data/silver/daily_bars_adjusted.parquet');

CREATE OR REPLACE VIEW execution_universe AS
SELECT * FROM read_parquet('data/gold/execution_universe.parquet');

CREATE OR REPLACE VIEW research_universe AS
SELECT * FROM read_parquet('data/gold/research_universe.parquet');

CREATE OR REPLACE VIEW trading_calendar AS
SELECT * FROM read_parquet('data/silver/trading_calendar.parquet');

CREATE OR REPLACE VIEW adjust_factors AS
SELECT * FROM read_parquet('data/silver/adjust_factors.parquet');

CREATE OR REPLACE VIEW corporate_actions_candidates AS
SELECT * FROM read_parquet('data/silver/corporate_actions_candidates.parquet');

CREATE OR REPLACE VIEW limit_status_daily AS
SELECT * FROM read_parquet('data/silver/limit_status_daily.parquet');

CREATE OR REPLACE VIEW suspension_daily AS
SELECT * FROM read_parquet('data/silver/suspension_daily.parquet');

CREATE OR REPLACE VIEW st_status_daily AS
SELECT * FROM read_parquet('data/silver/st_status_daily.parquet');
