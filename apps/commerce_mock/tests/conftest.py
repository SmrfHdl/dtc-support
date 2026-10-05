import os

import pytest

from commerce_mock.settings import DATABASE_URL_ENV


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        pytest.skip(f"{DATABASE_URL_ENV} is not set (start deploy/compose first)")
    return url
