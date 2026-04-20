from __future__ import annotations

import asyncio
import math
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from general_api.models import (
    CheckoutRequest,
    CourierCreate,
    CourierLocationUpdate,
    DeliveredWebhook,
    RestaurantCreate,
    RestaurantReadyWebhook,
    UserCreate,
)


# ─── Modelo para novo webhook ────────────────────────────────────────────
from pydantic import BaseModel, Field


class CourierAtRestaurantWebhook(BaseModel):
    order_id: str = Field(..., min_length=1)
    courier_id: str = Field(..., min_length=1)


# ─────────────────────────────────────────────────────────────────────────


def _require_http_base_url(env_name: str, value: Optional[str]) -> str:
    if not value:
        raise HTTPException(
            status_code=424,
            detail=f"Dependência ausente: configure {env_name}",
        )
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=500, detail=f"URL inválida em {env_name}"
        )
    return value.rstrip("/")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dl = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


DATABASE_SERVICE_URL = os.getenv(
    "DATABASE_SERVICE_URL", "http://database-service:8000"
).rstrip("/")
ORDER_SERVICE_URL = os.getenv(
    "ORDER_SERVICE_URL", "http://order-service:8002"
).rstrip("/")
ROUTE_SERVICE_URL = os.getenv(
    "ROUTE_SERVICE_URL", "http://route-service:8001"
).rstrip("/")

SIM_RESTAURANT_URL = os.getenv("SIM_RESTAURANT_URL")
SIM_COURIER_URL = os.getenv("SIM_COURIER_URL")

TIMEOUT_DB_S = float(os.getenv("TIMEOUT_DB_S", "30"))
TIMEOUT_ORDER_S = float(os.getenv("TIMEOUT_ORDER_S", "15"))
TIMEOUT_ROUTE_S = float(os.getenv("TIMEOUT_ROUTE_S", "60"))
TIMEOUT_SIM_S = float(os.getenv("TIMEOUT_SIM_S", "15"))

ARRIVAL_THRESHOLD_M = float(os.getenv("ARRIVAL_THRESHOLD_M", "10"))



async def _drain_confirmed_orders(app: FastAPI):
    """Background loop: pega pedidos CONFIRMED órfãos e processa-os.
    Isso garante que pedidos cujo checkout falhou no meio sejam drenados."""
    await asyncio.sleep(15)  # espera serviços subirem
    while True:
        try:
            client: httpx.AsyncClient = app.state.http
            sim_restaurant_url = SIM_RESTAURANT_URL.rstrip("/") if SIM_RESTAURANT_URL else None
            sim_courier_url = SIM_COURIER_URL.rstrip("/") if SIM_COURIER_URL else None
            if not sim_restaurant_url or not sim_courier_url:
                await asyncio.sleep(10)
                continue

            # 1. Busca pedidos CONFIRMED (órfãos)
            try:
                confirmed = await _request_json(
                    client, "GET",
                    f"{ORDER_SERVICE_URL}/pedidos/orders/status/CONFIRMED",
                    timeout_s=10,
                )
            except Exception:
                await asyncio.sleep(5)
                continue

            if not isinstance(confirmed, list) or not confirmed:
                await asyncio.sleep(5)
                continue

            # Limita a 10 por ciclo para não sobrecarregar
            batch = confirmed[:10]
            print(f"[Drainer] {len(confirmed)} pedidos CONFIRMED encontrados, processando {len(batch)}...")

            for order in batch:
                order_id = order.get("order_id")
                customer_id = order.get("customer_id")
                restaurant_id = order.get("restaurant_id")
                if not order_id or not restaurant_id:
                    continue

                try:
                    # 2. Pegar dados do restaurante e cliente
                    rest_data = await _request_json(
                        client, "GET",
                        f"{DATABASE_SERVICE_URL}/cadastro/restaurantes/{restaurant_id}",
                        timeout_s=10,
                    )
                    rest_lat, rest_lon = _coerce_lat_lon(rest_data)

                    user_lat, user_lon = rest_lat + 0.01, rest_lon + 0.01  # fallback
                    if customer_id:
                        try:
                            user_data = await _request_json(
                                client, "GET",
                                f"{DATABASE_SERVICE_URL}/cadastro/usuarios/{customer_id}",
                                timeout_s=10,
                            )
                            user_lat, user_lon = _coerce_lat_lon(user_data)
                        except Exception:
                            pass

                    # 3. Pegar entregador livre
                    drivers = await _request_json(
                        client, "GET",
                        f"{ORDER_SERVICE_URL}/pedidos/drivers/status/free?limit=5",
                        timeout_s=10,
                    )
                    if not isinstance(drivers, list) or not drivers:
                        continue

                    best_idx, best_dist = 0, float("inf")
                    for i, d in enumerate(drivers):
                        try:
                            dlat, dlon = _coerce_lat_lon(d)
                            dist = _haversine_m(rest_lat, rest_lon, dlat, dlon)
                            if dist < best_dist:
                                best_dist = dist
                                best_idx = i
                        except Exception:
                            continue

                    driver = drivers[best_idx]
                    c_id = _coerce_driver_id(driver)
                    c_lat, c_lon = _coerce_lat_lon(driver)

                    # 4. Atribuir entregador (PREPARING)
                    await _request_json(
                        client, "PATCH",
                        f"{ORDER_SERVICE_URL}/pedidos/orders/{order_id}/status",
                        timeout_s=10,
                        json={"status": "PREPARING", "entregador_id": c_id},
                    )

                    # 5. Notificar restaurante (com dados stateless)
                    route_to_client = _interpolate_route(rest_lat, rest_lon, user_lat, user_lon, 5)
                    route_to_rest = _interpolate_route(c_lat, c_lon, rest_lat, rest_lon, 3)

                    await _request_json(
                        client, "POST",
                        f"{sim_restaurant_url}/simulador/restaurante/prepare",
                        timeout_s=10,
                        json={
                            "order_id": order_id,
                            "restaurant_id": restaurant_id,
                            "driver_id": c_id,
                            "route_to_client": route_to_client,
                        },
                    )

                    # 6. Enviar entregador ao restaurante
                    await _request_json(
                        client, "POST",
                        f"{sim_courier_url}/simulador/entregador/go-to-restaurant",
                        timeout_s=10,
                        json={
                            "order_id": order_id,
                            "courier_id": c_id,
                            "route": route_to_rest,
                            "restaurant": {"lat": rest_lat, "lon": rest_lon},
                            "customer": {"lat": user_lat, "lon": user_lon},
                        },
                    )

                    print(f"[Drainer] ✅ Pedido {order_id[:8]} → PREPARING (driver {c_id[:8]})")

                except Exception as e:
                    print(f"[Drainer] ❌ Pedido {order_id[:8]}: {type(e).__name__}: {e}")
                    continue

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Drainer] Erro no loop: {e}")

        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    app.state.http = httpx.AsyncClient(
        headers={"User-Agent": "general-api"},
        limits=limits,
        timeout=30.0,
    )
    drain_task = asyncio.create_task(_drain_confirmed_orders(app))
    try:
        yield
    finally:
        drain_task.cancel()
        await app.state.http.aclose()


app = FastAPI(title="DijkFood - API Geral (Gateway)", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    timeout_s: float,
    json: Any | None = None,
) -> Any:
    try:
        resp = await client.request(method, url, json=json, timeout=timeout_s)
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504, detail=f"Timeout ao chamar dependência: {url}"
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=502, detail=f"Falha ao chamar dependência: {url}"
        )

    if resp.status_code == 404:
        raise HTTPException(
            status_code=424, detail=f"Dependência não expõe endpoint: {url}"
        )

    if 500 <= resp.status_code <= 599:
        raise HTTPException(
            status_code=502,
            detail=f"Dependência com erro ({resp.status_code}): {url}",
        )

    if resp.status_code < 200 or resp.status_code >= 300:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    if not resp.content:
        return None
    return resp.json()


def _rota_para_pontos(rota_data: Any) -> list[dict[str, float]]:
    """Converte uma rota do route-service (com 'percursos') em uma lista de pontos {lat, lon}."""

    if not isinstance(rota_data, dict):
        return []

    percursos = rota_data.get("percursos")
    if not isinstance(percursos, list):
        return []

    pontos: list[dict[str, float]] = []
    for percurso in percursos:
        if not isinstance(percurso, dict):
            continue

        for key in ("ponto_origem", "ponto_fim"):
            ponto = percurso.get(key)
            if not isinstance(ponto, dict):
                continue
            lat = ponto.get("lat")
            lon = ponto.get("lon")
            if lat is None or lon is None:
                continue
            pontos.append({"lat": float(lat), "lon": float(lon)})

    # Remove duplicatas consecutivas
    compactado: list[dict[str, float]] = []
    for p in pontos:
        if not compactado or p != compactado[-1]:
            compactado.append(p)
    return compactado


def _coerce_lat_lon(obj: dict[str, Any]) -> tuple[float, float]:
    lat = obj.get("lat")
    if lat is None:
        lat = obj.get("latitude")
    if lat is None:
        lat = obj.get("endereco_latitude")

    lon = obj.get("lon")
    if lon is None:
        lon = obj.get("lng")
    if lon is None:
        lon = obj.get("longitude")
    if lon is None:
        lon = obj.get("endereco_longitude")

    if lat is None or lon is None:
        raise HTTPException(
            status_code=502,
            detail="Payload inválido da dependência (coords ausentes)",
        )

    return float(lat), float(lon)


def _coerce_driver_id(obj: dict[str, Any]) -> str:
    for k in ("driver_id", "courier_id", "entregador_id", "id"):
        v = obj.get(k)
        if v:
            return str(v)
    raise HTTPException(
        status_code=502,
        detail="Payload inválido da dependência (id do driver ausente)",
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/usuarios")
async def listar_usuarios():
    client: httpx.AsyncClient = app.state.http
    return await _request_json(
        client,
        "GET",
        f"{DATABASE_SERVICE_URL}/cadastro/usuarios",
        timeout_s=TIMEOUT_DB_S,
    )


@app.get("/restaurantes")
async def listar_restaurantes(page: int = 1, page_size: int = 20):
    if page < 1:
        raise HTTPException(status_code=422, detail="page deve ser >= 1")
    if page_size < 1 or page_size > 200:
        raise HTTPException(
            status_code=422, detail="page_size deve estar entre 1 e 200"
        )

    client: httpx.AsyncClient = app.state.http
    items = await _request_json(
        client,
        "GET",
        f"{DATABASE_SERVICE_URL}/cadastro/restaurantes",
        timeout_s=TIMEOUT_DB_S,
    )

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items[start:end],
    }


@app.get("/restaurantes/{rest_id}/itens")
async def listar_itens_restaurante(rest_id: str):
    client: httpx.AsyncClient = app.state.http
    return await _request_json(
        client,
        "GET",
        f"{DATABASE_SERVICE_URL}/cadastro/produtos/restaurante/{rest_id}",
        timeout_s=TIMEOUT_DB_S,
    )


@app.get("/entregadores/livres")
async def listar_entregadores_livres():
    client: httpx.AsyncClient = app.state.http
    return await _request_json(
        client,
        "GET",
        f"{ORDER_SERVICE_URL}/pedidos/drivers/status/free?limit=50",
        timeout_s=TIMEOUT_ORDER_S,
    )


@app.post("/usuarios", status_code=201)
async def cadastrar_usuario(body: UserCreate):
    client: httpx.AsyncClient = app.state.http
    user_id = str(uuid.uuid4())
    payload = {
        "user_id": user_id,
        "primeiro_nome": body.first_name,
        "ultimo_nome": body.last_name,
        "email": body.email,
        "telefone": body.phone,
        "endereco_latitude": body.lat,
        "endereco_longitude": body.lng,
    }
    return await _request_json(
        client,
        "POST",
        f"{DATABASE_SERVICE_URL}/cadastro/usuarios",
        json=payload,
        timeout_s=TIMEOUT_DB_S,
    )


@app.post("/restaurantes", status_code=201)
async def cadastrar_restaurante(body: RestaurantCreate):
    client: httpx.AsyncClient = app.state.http
    rest_id = str(uuid.uuid4())
    payload = {
        "rest_id": rest_id,
        "nome": body.name,
        "tipo_cozinha": body.cuisine_type,
        "endereco_latitude": body.lat,
        "endereco_longitude": body.lng,
    }
    return await _request_json(
        client,
        "POST",
        f"{DATABASE_SERVICE_URL}/cadastro/restaurantes",
        json=payload,
        timeout_s=TIMEOUT_DB_S,
    )


@app.post("/entregadores", status_code=201)
async def cadastrar_entregador(body: CourierCreate):
    client: httpx.AsyncClient = app.state.http
    courier_id = str(uuid.uuid4())

    # 1. Cadastro no PostgreSQL
    db_payload = {
        "entregador_id": courier_id,
        "nome": body.name,
        "tipo_veiculo": body.vehicle_type,
        "endereco_latitude": body.lat,
        "endereco_longitude": body.lng,
    }
    await _request_json(
        client,
        "POST",
        f"{DATABASE_SERVICE_URL}/cadastro/entregadores",
        json=db_payload,
        timeout_s=TIMEOUT_DB_S,
    )

    # 2. Inicialização no DynamoDB (Order Service) para torná-lo LIVRE
    order_payload = {"lat": body.lat, "lng": body.lng}
    await _request_json(
        client,
        "PUT",
        f"{ORDER_SERVICE_URL}/pedidos/drivers/{courier_id}/location",
        json=order_payload,
        timeout_s=TIMEOUT_ORDER_S,
    )

    return {"mensagem": "Entregador cadastrado e ativo!", "id": courier_id}


@app.post("/usuarios/batch", status_code=201)
async def cadastrar_usuarios_batch(body: list[UserCreate]):
    client: httpx.AsyncClient = app.state.http
    payload = []
    for u in body:
        payload.append(
            {
                "user_id": str(uuid.uuid4()),
                "primeiro_nome": u.first_name,
                "ultimo_nome": u.last_name,
                "email": u.email,
                "telefone": u.phone,
                "endereco_latitude": u.lat,
                "endereco_longitude": u.lng,
            }
        )
    return await _request_json(
        client,
        "POST",
        f"{DATABASE_SERVICE_URL}/cadastro/usuarios/batch",
        json=payload,
        timeout_s=TIMEOUT_DB_S,
    )


@app.post("/restaurantes/batch", status_code=201)
async def cadastrar_restaurantes_batch(body: list[RestaurantCreate]):
    client: httpx.AsyncClient = app.state.http
    payload = []
    for r in body:
        payload.append(
            {
                "rest_id": str(uuid.uuid4()),
                "nome": r.name,
                "tipo_cozinha": r.cuisine_type,
                "endereco_latitude": r.lat,
                "endereco_longitude": r.lng,
            }
        )
    return await _request_json(
        client,
        "POST",
        f"{DATABASE_SERVICE_URL}/cadastro/restaurantes/batch",
        json=payload,
        timeout_s=TIMEOUT_DB_S,
    )


@app.post("/entregadores/batch", status_code=201)
async def cadastrar_entregadores_batch(body: list[CourierCreate]):
    client: httpx.AsyncClient = app.state.http

    # 1. Preparar payloads e IDs
    db_payload = []
    entregadores_ids = []
    for e in body:
        cid = str(uuid.uuid4())
        entregadores_ids.append(cid)
        db_payload.append(
            {
                "entregador_id": cid,
                "nome": e.name,
                "tipo_veiculo": e.vehicle_type,
                "endereco_latitude": e.lat,
                "endereco_longitude": e.lng,
            }
        )

    # 2. Cadastro no PostgreSQL (Batch)
    await _request_json(
        client,
        "POST",
        f"{DATABASE_SERVICE_URL}/cadastro/entregadores/batch",
        json=db_payload,
        timeout_s=TIMEOUT_DB_S,
    )

    # 3. Inicialização no DynamoDB (Batch via Order Service)
    dynamo_payload = [
        {
            "driver_id": item["entregador_id"],
            "lat": item["endereco_latitude"],
            "lng": item["endereco_longitude"],
        }
        for item in db_payload
    ]

    await _request_json(
        client,
        "POST",
        f"{ORDER_SERVICE_URL}/pedidos/drivers/batch-location",
        json=dynamo_payload,
        timeout_s=TIMEOUT_ORDER_S,
    )

    return {"mensagem": f"{len(body)} Entregadores cadastrados e ativos!"}

def _interpolate_route(
    lat1: float, lon1: float, lat2: float, lon2: float, num_points: int = 5
) -> list[dict[str, float]]:
    """Gera pontos intermediários entre duas coordenadas (rota simplificada)."""
    return [
        {
            "lat": lat1 + (lat2 - lat1) * (i / num_points),
            "lon": lon1 + (lon2 - lon1) * (i / num_points),
        }
        for i in range(num_points + 1)
    ]


@app.post("/checkout", status_code=201)
async def checkout(body: CheckoutRequest):
    sim_restaurant = _require_http_base_url(
        "SIM_RESTAURANT_URL", SIM_RESTAURANT_URL
    )
    sim_courier = _require_http_base_url("SIM_COURIER_URL", SIM_COURIER_URL)

    client: httpx.AsyncClient = app.state.http

    # 1. Obter Cliente e Restaurante (paralelo)
    user_task = _request_json(
        client,
        "GET",
        f"{DATABASE_SERVICE_URL}/cadastro/usuarios/{body.customer_id}",
        timeout_s=TIMEOUT_DB_S,
    )
    rest_task = _request_json(
        client,
        "GET",
        f"{DATABASE_SERVICE_URL}/cadastro/restaurantes/{body.restaurant_id}",
        timeout_s=TIMEOUT_DB_S,
    )

    usuario, restaurante = await asyncio.gather(user_task, rest_task)

    user_lat, user_lon = _coerce_lat_lon(usuario)
    rest_lat, rest_lon = _coerce_lat_lon(restaurante)

    # 2. Gerar rotas simplificadas (sem route-service — 100x mais rápido)
    rota_final_pontos = _interpolate_route(rest_lat, rest_lon, user_lat, user_lon, 5)

    # 3. Criar pedido no Banco (Status Inicial: CONFIRMED)
    pedido = await _request_json(
        client,
        "POST",
        f"{ORDER_SERVICE_URL}/pedidos/orders",
        timeout_s=TIMEOUT_ORDER_S,
        json={
            "customer_id": body.customer_id,
            "restaurant_id": body.restaurant_id,
            "items": body.items,
            "total_value": body.total_value,
        },
    )

    order_id = pedido.get("order_id")
    if not order_id:
        raise HTTPException(
            status_code=502, detail="order-service não retornou order_id"
        )

    # 4. Reservar Entregador (haversine local, sem route-service)
    assigned = False
    courier_id = None

    for attempt in range(5):
        try:
            entregadores = await _request_json(
                client,
                "GET",
                f"{ORDER_SERVICE_URL}/pedidos/drivers/status/free?limit=20",
                timeout_s=TIMEOUT_ORDER_S,
            )

            if not isinstance(entregadores, list) or not entregadores:
                await asyncio.sleep(0.3)
                continue

            # Achar o mais próximo por haversine (instantâneo)
            best_idx = 0
            best_dist = float("inf")
            drivers_norm: list[dict[str, Any]] = []
            for d in entregadores[:20]:
                if not isinstance(d, dict):
                    continue
                try:
                    dlat, dlon = _coerce_lat_lon(d)
                    did = _coerce_driver_id(d)
                except HTTPException:
                    continue
                dist = _haversine_m(rest_lat, rest_lon, dlat, dlon)
                drivers_norm.append({"driver_id": did, "lat": dlat, "lon": dlon})
                if dist < best_dist:
                    best_dist = dist
                    best_idx = len(drivers_norm) - 1

            if not drivers_norm:
                await asyncio.sleep(0.3)
                continue

            c_id = drivers_norm[best_idx]["driver_id"]
            c_lat = drivers_norm[best_idx]["lat"]
            c_lon = drivers_norm[best_idx]["lon"]

            # Lock no banco: PREPARING + atribui entregador
            await _request_json(
                client,
                "PATCH",
                f"{ORDER_SERVICE_URL}/pedidos/orders/{order_id}/status",
                timeout_s=TIMEOUT_ORDER_S,
                json={"status": "PREPARING", "entregador_id": c_id},
            )

            assigned = True
            courier_id = c_id
            break

        except HTTPException as e:
            if e.status_code in (400, 409):
                await asyncio.sleep(0.2)
                continue
            raise e
        except ValueError:
            continue

    if not assigned:
        raise HTTPException(
            status_code=503,
            detail={"error": "Nenhum entregador disponível", "order_id": order_id},
        )

    # 5. Notifica Restaurante + Entregador (paralelo, fire-and-forget)
    rota_ao_restaurante = _interpolate_route(c_lat, c_lon, rest_lat, rest_lon, 3)

    async def push_restaurante():
        return await _request_json(
            client,
            "POST",
            f"{sim_restaurant}/simulador/restaurante/prepare",
            timeout_s=TIMEOUT_SIM_S,
            json={
                "order_id": order_id,
                "restaurant_id": body.restaurant_id,
                "driver_id": courier_id,
                "route_to_client": rota_final_pontos,
            },
        )

    async def push_entregador():
        return await _request_json(
            client,
            "POST",
            f"{sim_courier}/simulador/entregador/go-to-restaurant",
            timeout_s=TIMEOUT_SIM_S,
            json={
                "order_id": order_id,
                "courier_id": courier_id,
                "route": rota_ao_restaurante,
                "restaurant": {"lat": rest_lat, "lon": rest_lon},
                "customer": {"lat": user_lat, "lon": user_lon},
            },
        )

    try:
        await asyncio.gather(push_restaurante(), push_entregador())
    except HTTPException as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"error": e.detail, "order_id": order_id},
        )

    return {
        "order_id": order_id,
        "courier_id": courier_id,
    }


# ─── Webhooks (totalmente stateless) ────────────────────────────────────


@app.post("/webhook/restaurant-ready")
async def webhook_restaurant_ready(body: RestaurantReadyWebhook):
    """
    Restaurante terminou de preparar o pedido.
    Recebe driver_id e route_to_client ecoados pelo restaurante,
    marca READY_FOR_PICKUP, e manda o motoboy fazer pickup + entrega.
    NENHUM estado em memória é usado.
    """
    sim_courier = _require_http_base_url("SIM_COURIER_URL", SIM_COURIER_URL)
    client: httpx.AsyncClient = app.state.http

    if not body.driver_id or not body.route_to_client:
        raise HTTPException(
            status_code=400,
            detail="webhook restaurant-ready sem driver_id ou route_to_client",
        )

    # 1. Marca READY_FOR_PICKUP
    try:
        await _request_json(
            client,
            "PATCH",
            f"{ORDER_SERVICE_URL}/pedidos/orders/{body.order_id}/status",
            timeout_s=TIMEOUT_ORDER_S,
            json={"status": "READY_FOR_PICKUP", "entregador_id": body.driver_id},
        )
    except Exception as e:
        print(f"[Orquestrador] Erro READY_FOR_PICKUP {body.order_id}: {e}")

    # 2. Manda o motoboy buscar + entregar (fire-and-forget via background task)
    async def _do_pickup():
        try:
            await _request_json(
                client,
                "POST",
                f"{sim_courier}/simulador/entregador/pickup-and-deliver",
                timeout_s=TIMEOUT_SIM_S,
                json={
                    "order_id": body.order_id,
                    "courier_id": body.driver_id,
                    "route": body.route_to_client,
                },
            )
        except Exception as e:
            print(f"[Orquestrador] Erro pickup-and-deliver {body.order_id}: {e}")

    asyncio.create_task(_do_pickup())
    return {"status": "ok"}


@app.post("/webhook/courier-at-restaurant")
async def webhook_courier_at_restaurant(body: CourierAtRestaurantWebhook):
    """Motoboy chegou ao restaurante — apenas loga. O pickup é trigado pelo
    webhook do restaurante, não pelo courier."""
    print(f"[Orquestrador] Courier {body.courier_id} chegou ao restaurante (pedido {body.order_id})")
    return {"status": "ok"}


@app.put("/tracking/courier/{courier_id}/location")
async def tracking_update(courier_id: str, body: CourierLocationUpdate):
    client: httpx.AsyncClient = app.state.http

    async def proxy_to_dynamo():
        try:
            await _request_json(
                client,
                "PUT",
                f"{ORDER_SERVICE_URL}/pedidos/drivers/{courier_id}/location",
                timeout_s=TIMEOUT_ORDER_S,
                json={"lat": body.lat, "lng": body.lng, "order_id": body.order_id},
            )
        except Exception:
            pass  # GPS update é best-effort

    asyncio.create_task(proxy_to_dynamo())
    return {"status": "accepted"}


@app.post("/webhook/delivered")
async def webhook_delivered(body: DeliveredWebhook):
    """Motoboy entregou o pedido — atualiza status até DELIVERED e libera o driver."""
    client: httpx.AsyncClient = app.state.http

    # Transições intermediárias: PICKED_UP → IN_TRANSIT → DELIVERED
    for status in ["PICKED_UP", "IN_TRANSIT", "DELIVERED"]:
        try:
            await _request_json(
                client,
                "PATCH",
                f"{ORDER_SERVICE_URL}/pedidos/orders/{body.order_id}/status",
                timeout_s=TIMEOUT_ORDER_S,
                json={"status": status, "entregador_id": body.courier_id},
            )
        except HTTPException:
            pass  # Pode já ter passado esse status — OK

    # Sincroniza localização final do entregador (best-effort)
    try:
        driver_loc = await _request_json(
            client,
            "GET",
            f"{ORDER_SERVICE_URL}/pedidos/drivers/{body.courier_id}/location",
            timeout_s=TIMEOUT_ORDER_S,
        )
        last_lat, last_lng = _coerce_lat_lon(driver_loc)
        await _request_json(
            client,
            "PATCH",
            f"{DATABASE_SERVICE_URL}/cadastro/entregadores/{body.courier_id}/localizacao",
            timeout_s=TIMEOUT_DB_S,
            json={"lat": last_lat, "lng": last_lng},
        )
    except Exception as e:
        print(f"[Orquestrador] Loc sync falhou para {body.courier_id}: {e}")

    return {"status": "ok"}
