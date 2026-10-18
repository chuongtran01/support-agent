from psycopg_pool import ConnectionPool

from app.config import settings

pool = ConnectionPool(settings.database_url, open=False, kwargs={"autocommit": True})
