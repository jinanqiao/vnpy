CREATE TABLE IF NOT EXISTS risk.events (
    event_time timestamptz NOT NULL,
    event_id uuid NOT NULL DEFAULT gen_random_uuid(),
    severity text NOT NULL CHECK (severity IN ('info', 'warning', 'blocking', 'critical')),
    event_type text NOT NULL,
    strategy_name text,
    vt_symbol text,
    order_id text,
    message text NOT NULL,
    details_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    resolved boolean NOT NULL DEFAULT false,
    PRIMARY KEY (event_time, event_id)
);

SELECT create_hypertable('risk.events', 'event_time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_risk_events_open
    ON risk.events(severity, event_type, event_time DESC)
    WHERE resolved = false;
