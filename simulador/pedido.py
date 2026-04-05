import asyncio
import random

import httpx

from .config import Config
from .populacao import PopData
from . import http_client as hc


async def run_order(client: httpx.AsyncClient, config: Config, pop: PopData) -> str:
    user_idx = random.randrange(len(pop.user_ids))
    user_id = pop.user_ids[user_idx]
    user_lat, user_lon = pop.user_coords[user_idx]

    rest = random.choice(pop.restaurantes)
    driver_id = random.choice(pop.driver_ids)

    # 1. Criar pedido
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
    print(f"    order_id={order_id}")

    # 2. Fase restaurante
    t_rest = config.restaurant_time_s
    print(f"    [restaurante] aguardando {t_rest * 0.4:.1f}s → PREPARING")
    await asyncio.sleep(t_rest * 0.4)
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "PREPARING"},
    )

    print(f"    [restaurante] aguardando {t_rest * 0.6:.1f}s → READY_FOR_PICKUP")
    await asyncio.sleep(t_rest * 0.6)
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "READY_FOR_PICKUP"},
    )

    # 3. Fase entregador
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "PICKED_UP", "entregador_id": driver_id},
    )
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "IN_TRANSIT", "entregador_id": driver_id},
    )

    # 4. Calcular rota
    resp_rota = await hc.request(
        client, "POST", f"{config.rotas_url}/rotas/rota-entrega", "route_entrega",
        json={"origem": {"lat": rest.lat, "lon": rest.lon}, "destino": {"lat": user_lat, "lon": user_lon}},
    )
    distancia = resp_rota["dados_rota"]["distancia_metros"]
    t_seg = (distancia / config.delivery_speed_mps) * config.delivery_time_multiplier
    n_puts = max(1, int(t_seg / 0.1))
    print(f"    [entrega] distancia={distancia:.0f}m | T_seg={t_seg:.1f}s | {n_puts} PUTs de localização")

    # 5. Loop de localização (100ms)
    for i in range(n_puts):
        frac = i / n_puts
        lat = rest.lat + (user_lat - rest.lat) * frac
        lon = rest.lon + (user_lon - rest.lon) * frac
        await hc.request(
            client, "PUT", f"{config.pedidos_url}/pedidos/drivers/{driver_id}/location", "location_put",
            json={"lat": lat, "lng": lon, "order_id": order_id},
        )
        await asyncio.sleep(0.1)

    # 6. DELIVERED
    await hc.request(
        client, "PATCH", f"{config.pedidos_url}/pedidos/orders/{order_id}/status", "status_patch",
        json={"status": "DELIVERED"},
    )

    return order_id
