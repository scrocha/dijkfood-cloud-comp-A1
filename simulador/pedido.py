import asyncio
import random

import httpx

from .config import Config
from .populacao import PopData
from . import http_client as hc


async def _client_poll(client: httpx.AsyncClient, config: Config, order_id: str, done: asyncio.Event):
    """Task cliente: GET a cada 1-3 min até DELIVERED ou evento de parada."""
    url = f"{config.pedidos_url}/pedidos/orders/{order_id}"
    while not done.is_set():
        wait = random.uniform(60, 180)
        try:
            await asyncio.wait_for(done.wait(), timeout=wait)
            break  # pedido entregue
        except asyncio.TimeoutError:
            pass  # hora de fazer GET
        body = await hc.request(client, "GET", url, "order_get")
        if body.get("status") == "DELIVERED":
            break


async def _pipeline(client: httpx.AsyncClient, config: Config, pop: PopData,
                    order_id: str, rest, user_lat: float, user_lon: float,
                    driver_id: str, done: asyncio.Event):
    """Task pipeline: PATCHs restaurante → entregador → rota → PUTs → DELIVERED."""
    # 1. Fase restaurante
    t_rest = config.restaurant_time_s
    await asyncio.sleep(t_rest * 0.4)
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "PREPARING"},
    )

    await asyncio.sleep(t_rest * 0.6)
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "READY_FOR_PICKUP"},
    )

    # 2. Fase entregador
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "PICKED_UP", "entregador_id": driver_id},
    )
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "IN_TRANSIT", "entregador_id": driver_id},
    )

    # 3. Calcular rota
    resp_rota = await hc.request(
        client, "POST", f"{config.rotas_url}/rotas/rota-entrega", "route_entrega",
        json={"origem": {"lat": rest.lat, "lon": rest.lon}, "destino": {"lat": user_lat, "lon": user_lon}},
    )
    if "dados_rota" in resp_rota:
        distancia = resp_rota["dados_rota"]["distancia_metros"]
        t_seg = (distancia / config.delivery_speed_mps) * config.delivery_time_multiplier
    else:
        t_seg = float(random.randint(180, 300))

    n_puts = max(1, int(t_seg / 0.1))

    # 4. Loop de localização (100ms)
    url_loc = f"{config.pedidos_url}/pedidos/drivers/{driver_id}/location"
    for i in range(n_puts):
        frac = i / n_puts
        lat = rest.lat + (user_lat - rest.lat) * frac
        lon = rest.lon + (user_lon - rest.lon) * frac
        await hc.request(
            client, "PUT", url_loc, "location_put",
            json={"lat": lat, "lng": lon, "order_id": order_id},
        )
        await asyncio.sleep(0.1)

    # 5. DELIVERED
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "DELIVERED"},
    )

    done.set()


async def run_order(client: httpx.AsyncClient, config: Config, pop: PopData) -> str:
    user_idx = random.randrange(len(pop.user_ids))
    user_id = pop.user_ids[user_idx]
    user_lat, user_lon = pop.user_coords[user_idx]

    rest = random.choice(pop.restaurantes)
    driver_id = random.choice(pop.driver_ids)

    # Criar pedido
    items = [
        {"nome": p["nome"], "quantidade": 1, "preco": round(random.uniform(10, 50), 2)}
        for p in rest.produtos
    ]
    total = round(sum(i["preco"] for i in items), 2)

    resp = await hc.request(
        client, "POST", f"{config.pedidos_url}/pedidos/orders", "order_create",
        json={"customer_id": user_id, "restaurant_id": rest.id, "items": items, "total_value": total},
    )
    order_id = resp["order_id"]

    # Duas tasks concorrentes após o POST
    done = asyncio.Event()
    pipeline_task = asyncio.create_task(
        _pipeline(client, config, pop, order_id, rest, user_lat, user_lon, driver_id, done)
    )
    client_task = asyncio.create_task(
        _client_poll(client, config, order_id, done)
    )

    await pipeline_task
    await client_task

    return order_id
