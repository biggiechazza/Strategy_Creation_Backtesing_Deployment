# Read immutable strategy versions from PostgreSQL.
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus

def get_strategy_version(conn, *, version_id: int) -> dict:
    '''Return a stored strategy version or report a missing ID.'''
    with conn.cursor(row_factory = dict_row) as cur:
        cur.execute('''SELECT strategy_version_id, entrypoint_name, source_code FROM public.strategy_versions WHERE strategy_version_id = %s''', (version_id,))
        version = cur.fetchone()
    if version is None:
        raise ValueError(f'Strategy version {version_id} does not exist')
    return version


    
# This class gives dupe name errors their own type, so the write node can catch them and ask for another name without confusing them with other errors.
class Duplicate_Name_Prob(ValueError):
    # The requested strategy name is already in use
    pass

def create_strategy_with_initial_version(conn,*,name: str,source_code: str,entrypoint_name: str,description: str | None = None,) -> dict:
   # Create and commit a new strategy and version 1 together

# Check the required fields are nonblank strings before starting the DB work
    for field_name, value in (('name', name),('source_code', source_code),('entrypoint_name', entrypoint_name),):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{field_name} must be a nonblank string.')

    # Description can be left out, but a supplied value needs to be text
    if description is not None and not isinstance(description, str):
        raise ValueError('description must be a string or None.')

    # Require an idle connection so this function owns the transaction and final commit
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Creating a strategy requires an idle connection.')

    # Clean up name/description whitespace, keep source and entrypoint exactly as supplied
    name = name.strip()

# A blank description becomes None, which is stored as SQL NULL
    if description is not None:
        description = description.strip() or None

# Save both records together. A successful exit commits, an exception rolls both back
    with conn.transaction():

        # dict_row lets us grab returned values using column names as dictionary keys
        with conn.cursor(row_factory=dict_row) as cur:

            # Create the strategy and grab its Postgres generated ID with RETURNING
            # ON CONFLICT skips taken names, Psycopg fills %s from the tuple below
            cur.execute("""INSERT INTO public.strategies (name, description)
                VALUES (%s, %s)ON CONFLICT (name) DO NOTHING RETURNING strategy_id""",(name, description),)

            strategy = cur.fetchone()
            # No returned row means the name is taken, so stop before creating a version
            if strategy is None:
                raise Duplicate_Name_Prob (f'A strategy named {name!r} already exists.')

            # Link version 1 to the new strategy, saving its source and exact entrypoint
            # PostgreSQL generates the version ID separately from the version number
            cur.execute("""INSERT INTO public.strategy_versions (strategy_id, version_number, entrypoint_name, source_code)
                VALUES (%s, 1, %s, %s)RETURNING strategy_version_id""", (strategy['strategy_id'], entrypoint_name, source_code),)

            version = cur.fetchone()

            # A missing version ID is an error, raising here also rolls back the strategy insert
            if version is None:
                raise RuntimeError('INSERT did not return a strategy_version_id.')
    # Return both IDs after commit so the write node can put them into workflow state
    return {'strategy_id': strategy['strategy_id'], 'strategy_version_id': version['strategy_version_id'],}
