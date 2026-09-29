from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from .config import database_url


@contextmanager
def conn():
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        yield connection
