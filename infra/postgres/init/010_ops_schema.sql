CREATE TABLE IF NOT EXISTS ops.data_versions (
    version_id text PRIMARY KEY,
    created_at timestamptz NOT NULL,
    data_root text NOT NULL,
    latest_trade_date date,
    manifest_json jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.dataset_snapshots (
    version_id text NOT NULL REFERENCES ops.data_versions(version_id) ON DELETE CASCADE,
    name text NOT NULL,
    layer text NOT NULL,
    source text NOT NULL,
    path text NOT NULL,
    sha256 text NOT NULL,
    rows bigint,
    date_min date,
    date_max date,
    symbol_count integer,
    pit_status text NOT NULL,
    PRIMARY KEY (version_id, name)
);

CREATE TABLE IF NOT EXISTS ops.quality_check_results (
    check_id text PRIMARY KEY,
    version_id text,
    dataset_name text NOT NULL,
    check_name text NOT NULL,
    mode text NOT NULL,
    severity text NOT NULL,
    status text NOT NULL,
    details_json jsonb NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.run_metadata (
    run_id text PRIMARY KEY,
    run_type text NOT NULL,
    strategy_name text NOT NULL,
    data_version_id text,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL,
    output_paths_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    config_hash text,
    summary_json jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_quality_check_results_version
    ON ops.quality_check_results(version_id, mode, status);

CREATE INDEX IF NOT EXISTS idx_run_metadata_data_version
    ON ops.run_metadata(data_version_id, started_at DESC);
