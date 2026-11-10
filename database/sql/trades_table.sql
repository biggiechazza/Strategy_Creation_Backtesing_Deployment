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
