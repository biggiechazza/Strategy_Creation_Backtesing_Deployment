# Open PostgreSQL connections without storing creds

import psycopg


def connect_database(*, database, user, password, host = "localhost", port = 5432):
    # Returns an idle connection, repos open short write transactions
    return psycopg.connect(
        host = host,
        port = port,
        dbname = database,
        user = user,
        password = password,
        autocommit = True)
