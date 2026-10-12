import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_unknown_route_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/nope")).status_code == 404
