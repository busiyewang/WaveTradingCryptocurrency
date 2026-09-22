CREATE TABLE IF NOT EXISTS quant_runs (
    run_id VARCHAR(32) PRIMARY KEY,
    status VARCHAR(16) NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    created_at BIGINT NOT NULL,
    finished_at BIGINT,
    manifest_json LONGTEXT NOT NULL,
    summary_json LONGTEXT
);
CREATE TABLE IF NOT EXISTS quant_candles (
    run_id VARCHAR(32) NOT NULL,
    inst VARCHAR(64) NOT NULL,
    bar VARCHAR(8) NOT NULL,
    open_ts BIGINT NOT NULL,
    candle_json LONGTEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES quant_runs(run_id),
    PRIMARY KEY (run_id, inst, bar, open_ts)
);
CREATE TABLE IF NOT EXISTS quant_signal_observations (
    run_id VARCHAR(32) NOT NULL,
    signal_key VARCHAR(64) NOT NULL,
    observed_at BIGINT NOT NULL,
    extreme_at BIGINT NOT NULL,
    locked INTEGER NOT NULL CHECK (locked IN (0, 1)),
    snapshot_json LONGTEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES quant_runs(run_id),
    PRIMARY KEY (run_id, signal_key, observed_at)
);
CREATE TABLE IF NOT EXISTS quant_evaluations (
    run_id VARCHAR(32) NOT NULL,
    observed_at BIGINT NOT NULL,
    signal_key VARCHAR(64),
    action VARCHAR(8) NOT NULL CHECK (action IN ('wait', 'long', 'short')),
    reason VARCHAR(64) NOT NULL,
    snapshot_json LONGTEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES quant_runs(run_id),
    PRIMARY KEY (run_id, observed_at)
);
