CREATE TABLE IF NOT EXISTS live.signals (
    signal_time timestamptz NOT NULL,
    strategy_name text NOT NULL,
    vt_symbol text NOT NULL,
    signal_date date NOT NULL,
    side text NOT NULL CHECK (side IN ('buy', 'sell', 'hold')),
    target_weight double precision,
    target_volume double precision,
    score double precision,
    data_version_id text,
    run_id text,
    details_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (signal_time, strategy_name, vt_symbol)
);

SELECT create_hypertable('live.signals', 'signal_time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS live.orders (
    order_time timestamptz NOT NULL,
    order_id text NOT NULL,
    strategy_name text NOT NULL,
    vt_symbol text NOT NULL,
    direction text NOT NULL,
    offset_type text,
    order_type text,
    price double precision,
    volume double precision NOT NULL,
    traded double precision NOT NULL DEFAULT 0,
    status text NOT NULL,
    gateway_name text,
    data_version_id text,
    run_id text,
    raw_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (order_time, order_id)
);

SELECT create_hypertable('live.orders', 'order_time', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS uq_live_orders_order_id ON live.orders(order_id, order_time);

CREATE TABLE IF NOT EXISTS live.trades (
    trade_time timestamptz NOT NULL,
    trade_id text NOT NULL,
    order_id text,
    strategy_name text NOT NULL,
    vt_symbol text NOT NULL,
    direction text NOT NULL,
    price double precision NOT NULL,
    volume double precision NOT NULL,
    turnover double precision,
    commission double precision,
    gateway_name text,
    raw_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (trade_time, trade_id)
);

SELECT create_hypertable('live.trades', 'trade_time', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS uq_live_trades_trade_id ON live.trades(trade_id, trade_time);

CREATE TABLE IF NOT EXISTS live.positions (
    snapshot_time timestamptz NOT NULL,
    account_id text NOT NULL,
    vt_symbol text NOT NULL,
    volume double precision NOT NULL,
    available double precision NOT NULL,
    frozen double precision NOT NULL DEFAULT 0,
    cost_price double precision,
    last_price double precision,
    market_value double precision,
    pnl double precision,
    raw_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_time, account_id, vt_symbol)
);

SELECT create_hypertable('live.positions', 'snapshot_time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS live.account_snapshots (
    snapshot_time timestamptz NOT NULL,
    account_id text NOT NULL,
    balance double precision NOT NULL,
    available double precision NOT NULL,
    frozen double precision NOT NULL DEFAULT 0,
    market_value double precision,
    total_asset double precision,
    raw_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_time, account_id)
);

SELECT create_hypertable('live.account_snapshots', 'snapshot_time', if_not_exists => TRUE);
