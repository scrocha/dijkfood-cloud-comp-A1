import time
import httpx
from . import metrics


async def request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    operation: str,
    json: dict = None,
) -> dict:
    t0 = time.monotonic()
    resp = await client.request(method, url, json=json)
    lat_ms = (time.monotonic() - t0) * 1000
    metrics.record(operation, lat_ms)
    return resp.json()
