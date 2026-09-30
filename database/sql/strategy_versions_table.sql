CREATE TABLE strategy_versions (
    strategy_version_id BIGINT GENERATED ALWAYS AS IDENTITY (START WITH 1000) PRIMARY KEY,
    strategy_id BIGINT NOT NULL REFERENCES strategies (strategy_id) ON DELETE RESTRICT,
    version_number INTEGER NOT NULL,
    entrypoint_name TEXT,
    source_code TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT strategy_versions_number_positive CHECK (version_number >= 1),
    CONSTRAINT strategy_versions_entrypoint_not_blank
        CHECK (entrypoint_name IS NULL OR btrim(entrypoint_name) <> ''),
    CONSTRAINT strategy_versions_source_not_blank CHECK (btrim(source_code) <> ''),
    CONSTRAINT strategy_versions_strategy_number_unique UNIQUE (strategy_id, version_number)
);

CREATE FUNCTION prevent_strategy_version_update()
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
       OR (OLD.created_at IS NOT NULL
           AND NEW.created_at IS DISTINCT FROM OLD.created_at) THEN
        RAISE EXCEPTION 'strategy version identity and populated fields are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER strategy_versions_no_update
BEFORE UPDATE ON strategy_versions
FOR EACH ROW EXECUTE FUNCTION prevent_strategy_version_update();
