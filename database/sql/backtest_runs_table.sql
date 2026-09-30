CREATE TABLE public.backtest_runs (
    run_id BIGINT GENERATED ALWAYS AS IDENTITY (START WITH 1000) PRIMARY KEY,
    strategy_version_id BIGINT NOT NULL
        REFERENCES public.strategy_versions (strategy_version_id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    error_message TEXT,

    dataset_ref TEXT,
    dataset_sha256 VARCHAR(64),
    engine_sha256 VARCHAR(64),
    quantity SMALLINT NOT NULL,
    cost_points_per_trade DOUBLE PRECISION NOT NULL,

    total_trades BIGINT,
    winning_trades BIGINT,
    losing_trades BIGINT,
    win_rate DOUBLE PRECISION,
    total_net_pnl DOUBLE PRECISION,
    average_trade DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,

    CONSTRAINT backtest_runs_status_valid
        CHECK (status IN ('running', 'completed', 'failed')),
    CONSTRAINT backtest_runs_finished_after_started
        CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
    CONSTRAINT backtest_runs_dataset_ref_not_blank
        CHECK (dataset_ref IS NULL OR btrim(dataset_ref) <> ''),
    CONSTRAINT backtest_runs_dataset_hash_format
        CHECK (dataset_sha256 IS NULL OR dataset_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT backtest_runs_engine_hash_format
        CHECK (engine_sha256 IS NULL OR engine_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT backtest_runs_quantity_range
        CHECK (quantity BETWEEN 1 AND 10),
    CONSTRAINT backtest_runs_cost_finite_nonnegative
        CHECK (cost_points_per_trade >= 0
            AND cost_points_per_trade < 'Infinity'::double precision),
    CONSTRAINT backtest_runs_counts_nonnegative
        CHECK ((total_trades IS NULL OR total_trades >= 0)
            AND (winning_trades IS NULL OR winning_trades >= 0)
            AND (losing_trades IS NULL OR losing_trades >= 0)),
    CONSTRAINT backtest_runs_metrics_valid
        CHECK ((win_rate IS NULL OR win_rate BETWEEN 0 AND 1)
            AND (total_net_pnl IS NULL OR
                (total_net_pnl > '-Infinity'::double precision
                 AND total_net_pnl < 'Infinity'::double precision))
            AND (average_trade IS NULL OR
                (average_trade > '-Infinity'::double precision
                 AND average_trade < 'Infinity'::double precision))
            AND (profit_factor IS NULL OR
                (profit_factor >= 0
                 AND profit_factor < 'Infinity'::double precision))
            AND (max_drawdown IS NULL OR
                (max_drawdown >= 0
                 AND max_drawdown < 'Infinity'::double precision))),
    CONSTRAINT backtest_runs_lifecycle_valid CHECK (
        (status = 'running'
            AND finished_at IS NULL
            AND error_message IS NULL
            AND total_trades IS NULL AND winning_trades IS NULL
            AND losing_trades IS NULL AND win_rate IS NULL
            AND total_net_pnl IS NULL AND average_trade IS NULL
            AND profit_factor IS NULL AND max_drawdown IS NULL)
        OR
        (status = 'failed'
            AND (error_message IS NULL OR btrim(error_message) <> '')
            AND total_trades IS NULL AND winning_trades IS NULL
            AND losing_trades IS NULL AND win_rate IS NULL
            AND total_net_pnl IS NULL AND average_trade IS NULL
            AND profit_factor IS NULL AND max_drawdown IS NULL)
        OR
        (status = 'completed'
            AND error_message IS NULL
            AND total_trades IS NOT NULL AND winning_trades IS NOT NULL
            AND losing_trades IS NOT NULL AND win_rate IS NOT NULL
            AND total_net_pnl IS NOT NULL AND average_trade IS NOT NULL
            AND max_drawdown IS NOT NULL
            AND winning_trades + losing_trades <= total_trades
            AND ((losing_trades = 0 AND profit_factor IS NULL)
              OR (losing_trades > 0 AND profit_factor IS NOT NULL)))
    )
);

CREATE INDEX backtest_runs_strategy_version_run_idx
    ON public.backtest_runs (strategy_version_id, run_id);
