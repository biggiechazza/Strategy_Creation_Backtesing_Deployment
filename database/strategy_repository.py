
# Reads *immutable* strategy versions from PostgreSQL

from psycopg.rows import dict_row

def get_strategy_version(conn, *, version_id: int) -> dict:
    with conn.cursor(row_factory = dict_row) as cur:
        cur.execute('''SELECT strategy_version_id, entrypoint_name, source_code FROM strategy_versions WHERE strategy_version_id = %s''', (version_id,))
        version = cur.fetchone()
    if version is None:
        raise ValueError(f"Strategy version {version_id} does not exist")
    return version
