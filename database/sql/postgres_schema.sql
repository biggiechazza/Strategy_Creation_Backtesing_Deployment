-- Week 2 initial PostgreSQL schema.
-- Run only against an EMPTY application database. Do not rerun on an existing
-- database or use this file as a substitute for later Alembic migrations.
-- Strategy source and backtest results are data, so no seed rows are included.

BEGIN;

-- One row identifies a logical strategy. The executable code lives in
-- strategy_versions, so a strategy may have many immutable versions.
CREATE TABLE public.strategies (
    strategy_id BIGINT GENERATED ALWAYS AS IDENTITY (START WITH 1000) PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT strategies_name_not_blank CHECK (btrim(name) <> '')
);

-- One row stores the complete Python source for one numbered strategy version.
-- Display versions as, for example, strategy 1000 / version 1 (not 1000.1).
CREATE TABLE public.strategy_versions (
    strategy_version_id BIGINT GENERATED ALWAYS AS IDENTITY (START WITH 1000) PRIMARY KEY,
    strategy_id BIGINT NOT NULL REFERENCES public.strategies (strategy_id) ON DELETE RESTRICT,
    version_number INTEGER NOT NULL,
    entrypoint_name TEXT,
    source_code TEXT NOT NULL,
    source_sha256 VARCHAR(64),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT strategy_versions_number_positive CHECK (version_number >= 1),
    CONSTRAINT strategy_versions_entrypoint_not_blank
        CHECK (entrypoint_name IS NULL OR btrim(entrypoint_name) <> ''),
    CONSTRAINT strategy_versions_source_not_blank CHECK (btrim(source_code) <> ''),
    CONSTRAINT strategy_versions_source_hash_format
        CHECK (source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT strategy_versions_strategy_number_unique UNIQUE (strategy_id, version_number)
);

-- The source and version identity cannot change. Optional metadata may be
-- filled in once later, but a populated value cannot be replaced or cleared.
-- A code change is represented by inserting a new version row.
CREATE FUNCTION public.prevent_strategy_version_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.strategy_version_id IS DISTINCT FROM OLD.strategy_version_id
       OR NEW.strategy_id IS DISTINCT FROM OLD.strategy_id
       OR NEW.version_number IS DISTINCT FROM OLD.version_number
       OR NEW.source_code IS DISTINCT FROM OLD.source_code
       OR (OLD.entrypoint_name IS NOT NULL
           AND NEW.entrypoint_name IS DISTINCT FROM OLD.entrypoint_name)
       OR (OLD.source_sha256 IS NOT NULL
           AND NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256)
       OR (OLD.created_at IS NOT NULL
           AND NEW.created_at IS DISTINCT FROM OLD.created_at) THEN
        RAISE EXCEPTION 'strategy version identity and populated fields are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER strategy_versions_no_update
BEFORE UPDATE ON public.strategy_versions
FOR EACH ROW EXECUTE FUNCTION public.prevent_strategy_version_update();

-- One row represents one execution attempt. Insert/commit it as running before
-- computation. In a later transaction, save trades and metrics and set completed.
-- On computation/save failure, mark failed in a separate transaction.
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

-- One row represents one completed trade. trade_number starts at 1 within
-- each run; the composite primary key indexes trades by run and order.
-- Local wall times plus optional UTC offsets preserve both naive and
-- offset-aware datetimes from the current CSV loader.
CREATE TABLE public.trades (
    run_id BIGINT NOT NULL REFERENCES public.backtest_runs (run_id) ON DELETE CASCADE,
    trade_number INTEGER NOT NULL,
    direction TEXT NOT NULL,
    quantity SMALLINT NOT NULL,
    entry_timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    entry_utc_offset INTERVAL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    exit_utc_offset INTERVAL,
    exit_price DOUBLE PRECISION NOT NULL,
    gross_points DOUBLE PRECISION NOT NULL,
    gross_pnl DOUBLE PRECISION NOT NULL,
    cost_dollars DOUBLE PRECISION NOT NULL,
    net_pnl DOUBLE PRECISION NOT NULL,

    CONSTRAINT trades_pkey PRIMARY KEY (run_id, trade_number),
    CONSTRAINT trades_number_positive CHECK (trade_number >= 1),
    CONSTRAINT trades_direction_valid CHECK (direction IN ('long', 'short')),
    CONSTRAINT trades_quantity_range CHECK (quantity BETWEEN 1 AND 10),
    CONSTRAINT trades_offsets_valid CHECK (
        (entry_utc_offset IS NULL OR
            (entry_utc_offset > INTERVAL '-24 hours'
             AND entry_utc_offset < INTERVAL '24 hours'))
        AND
        (exit_utc_offset IS NULL OR
            (exit_utc_offset > INTERVAL '-24 hours'
             AND exit_utc_offset < INTERVAL '24 hours'))
    ),
    CONSTRAINT trades_values_finite CHECK (
        entry_price > 0 AND entry_price < 'Infinity'::double precision
        AND exit_price > 0 AND exit_price < 'Infinity'::double precision
        AND gross_points > '-Infinity'::double precision
        AND gross_points < 'Infinity'::double precision
        AND gross_pnl > '-Infinity'::double precision
        AND gross_pnl < 'Infinity'::double precision
        AND cost_dollars >= 0 AND cost_dollars < 'Infinity'::double precision
        AND net_pnl > '-Infinity'::double precision
        AND net_pnl < 'Infinity'::double precision
    )
);

COMMIT;
