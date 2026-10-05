"""Runtime settings read from the environment."""

import os

DATABASE_URL_ENV = "COMMERCE_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql+asyncpg://dtc:dtc@localhost:5432/dtc"


def database_url() -> str:
    return os.environ.get(DATABASE_URL_ENV, DEFAULT_DATABASE_URL)
