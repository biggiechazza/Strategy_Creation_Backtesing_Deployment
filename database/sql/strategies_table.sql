CREATE TABLE strategies (
    strategy_id BIGINT GENERATED ALWAYS AS IDENTITY (START WITH 1000) PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT strategies_name_not_blank CHECK (btrim(name) <> '')
);
