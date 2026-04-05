import time
import httpx
from . import metrics


async def request_raw(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    operation: str,
    json: dict = None,
    silent: bool = False,
) -> tuple[dict, float]:
    t0 = time.monotonic()
    resp = await client.request(method, url, json=json)
    lat_ms = (time.monotonic() - t0) * 1000

    if not silent:
        print(f"  [{method}] {url} → {resp.status_code} ({lat_ms:.0f}ms)")

    metrics.record(operation, lat_ms)
    return resp.json(), lat_ms


async def request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    operation: str,
    json: dict = None,
) -> dict:
    result, _ = await request_raw(client, method, url, operation, json=json, silent=False)
    return result
