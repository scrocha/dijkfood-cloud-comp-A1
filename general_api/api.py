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
    CourierPickedUpWebhook,
    DeliveredWebhook,
    RestaurantCreate,
    RestaurantReadyWebhook,
    UserCreate,
)


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

TIMEOUT_DB_S = float(os.getenv("TIMEOUT_DB_S", "5"))
TIMEOUT_ORDER_S = float(os.getenv("TIMEOUT_ORDER_S", "5"))
TIMEOUT_ROUTE_S = float(os.getenv("TIMEOUT_ROUTE_S", "30"))
TIMEOUT_SIM_S = float(os.getenv("TIMEOUT_SIM_S", "5"))

ARRIVAL_THRESHOLD_M = float(os.getenv("ARRIVAL_THRESHOLD_M", "10"))


class _OrderState:
    def __init__(
        self,
        order_id: str,
        courier_id: str,
        restaurant: dict[str, float],
        customer: dict[str, float],
        route_to_restaurant: dict[str, Any],
        route_to_client: dict[str, Any],
    ):
        self.order_id = order_id
        self.courier_id = courier_id
        self.restaurant = restaurant
        self.customer = customer
        self.route_to_restaurant = route_to_restaurant
        self.route_to_client = route_to_client

        self.restaurant_ready = False
        self.courier_arrived = False
        self.order_ready_sent = False


_state_lock = asyncio.Lock()
_order_state: dict[str, _OrderState] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(headers={"User-Agent": "general-api"})
    try:
        yield
    finally:
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
        f"{ORDER_SERVICE_URL}/pedidos/drivers/status/free",
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


@app.post("/checkout", status_code=201)
async def checkout(body: CheckoutRequest):
    sim_restaurant = _require_http_base_url(
        "SIM_RESTAURANT_URL", SIM_RESTAURANT_URL
    )
    sim_courier = _require_http_base_url("SIM_COURIER_URL", SIM_COURIER_URL)

    client: httpx.AsyncClient = app.state.http

    # Paralelização das consultas iniciais (Usuário, Restaurante e Entregadores Livres)
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
    drivers_task = _request_json(
        client,
        "GET",
        f"{ORDER_SERVICE_URL}/pedidos/drivers/status/free",
        timeout_s=TIMEOUT_ORDER_S,
    )

    usuario, restaurante, entregadores = await asyncio.gather(
        user_task, rest_task, drivers_task
    )

    user_lat, user_lon = _coerce_lat_lon(usuario)
    rest_lat, rest_lon = _coerce_lat_lon(restaurante)

    if not isinstance(entregadores, list):
        raise HTTPException(
            status_code=502,
            detail="Payload inválido de entregadores disponíveis",
        )
    if not entregadores:
        raise HTTPException(status_code=409, detail="Sem entregadores livres")

    drivers_norm: list[dict[str, Any]] = []
    drivers_points: list[dict[str, float]] = []
    for d in entregadores:
        if not isinstance(d, dict):
            continue
        dlat, dlon = _coerce_lat_lon(d)
        did = _coerce_driver_id(d)
        drivers_norm.append({"driver_id": did, "lat": dlat, "lon": dlon})
        drivers_points.append({"lat": dlat, "lon": dlon})

    if not drivers_norm:
        raise HTTPException(
            status_code=502,
            detail="Lista de entregadores disponíveis sem dados válidos",
        )

    escolha = await _request_json(
        client,
        "POST",
        f"{ROUTE_SERVICE_URL}/rotas/entregador-mais-proximo",
        timeout_s=TIMEOUT_ROUTE_S,
        json={
            "restaurante": {"lat": rest_lat, "lon": rest_lon},
            "entregadores": drivers_points,
        },
    )

    idx = escolha.get("entregador_idx")
    if not isinstance(idx, int) or idx < 0 or idx >= len(drivers_norm):
        raise HTTPException(
            status_code=502,
            detail="Resposta inválida do route-service (entregador_idx)",
        )

    courier_id = drivers_norm[idx]["driver_id"]
    rota_ao_restaurante = escolha.get("rota_ao_restaurante")

    rota_final = await _request_json(
        client,
        "POST",
        f"{ROUTE_SERVICE_URL}/rotas/rota-entrega",
        timeout_s=TIMEOUT_ROUTE_S,
        json={
            "origem": {"lat": rest_lat, "lon": rest_lon},
            "destino": {"lat": user_lat, "lon": user_lon},
        },
    )

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

    async def push_restaurante():
        return await _request_json(
            client,
            "POST",
            f"{sim_restaurant}/simulador/restaurante/prepare",
            timeout_s=TIMEOUT_SIM_S,
            json={"order_id": order_id, "restaurant_id": body.restaurant_id},
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

    await _request_json(
        client,
        "PATCH",
        f"{ORDER_SERVICE_URL}/pedidos/orders/{order_id}/status",
        timeout_s=TIMEOUT_ORDER_S,
        json={"status": "PREPARING", "entregador_id": courier_id},
    )

    async with _state_lock:
        _order_state[order_id] = _OrderState(
            order_id=order_id,
            courier_id=courier_id,
            restaurant={"lat": rest_lat, "lon": rest_lon},
            customer={"lat": user_lat, "lon": user_lon},
            route_to_restaurant=rota_ao_restaurante,
            route_to_client=rota_final,
        )

    return {
        "order_id": order_id,
        "courier_id": courier_id,
        "route_to_restaurant": rota_ao_restaurante,
        "route_to_client": rota_final,
    }


async def _send_order_ready(order_id: str):
    sim_courier = _require_http_base_url("SIM_COURIER_URL", SIM_COURIER_URL)
    client: httpx.AsyncClient = app.state.http

    async with _state_lock:
        st = _order_state.get(order_id)
        if not st:
            return
        if st.order_ready_sent or not (
            st.restaurant_ready and st.courier_arrived
        ):
            return
        courier_id = st.courier_id

    await _request_json(
        client,
        "POST",
        f"{sim_courier}/simulador/entregador/order-ready",
        timeout_s=TIMEOUT_SIM_S,
        json={"order_id": order_id, "courier_id": courier_id},
    )

    async with _state_lock:
        st = _order_state.get(order_id)
        if st:
            st.order_ready_sent = True


@app.post("/webhook/restaurant-ready")
async def webhook_restaurant_ready(body: RestaurantReadyWebhook):
    client: httpx.AsyncClient = app.state.http

    async with _state_lock:
        st = _order_state.get(body.order_id)
        if not st:
            raise HTTPException(
                status_code=404, detail="Pedido não encontrado no orquestrador"
            )
        st.restaurant_ready = True

    try:
        await _request_json(
            client,
            "PATCH",
            f"{ORDER_SERVICE_URL}/pedidos/orders/{body.order_id}/status",
            timeout_s=TIMEOUT_ORDER_S,
            json={
                "status": "READY_FOR_PICKUP",
                "entregador_id": st.courier_id,
            },
        )
    except HTTPException as e:
        if e.status_code == 400:
            raise HTTPException(status_code=409, detail=e.detail)
        raise

    asyncio.create_task(_send_order_ready(body.order_id))
    return {"status": "ok"}


@app.put("/tracking/courier/{courier_id}/location")
async def tracking_update(courier_id: str, body: CourierLocationUpdate):
    client: httpx.AsyncClient = app.state.http

    async def proxy_to_dynamo():
        await _request_json(
            client,
            "PUT",
            f"{ORDER_SERVICE_URL}/pedidos/drivers/{courier_id}/location",
            timeout_s=TIMEOUT_ORDER_S,
            json={"lat": body.lat, "lng": body.lng, "order_id": body.order_id},
        )

    asyncio.create_task(proxy_to_dynamo())

    if body.order_id:
        async with _state_lock:
            st = _order_state.get(body.order_id)
            if st and st.courier_id == courier_id:
                dist = _haversine_m(
                    body.lat,
                    body.lng,
                    st.restaurant["lat"],
                    st.restaurant["lon"],
                )
                if dist <= ARRIVAL_THRESHOLD_M:
                    st.courier_arrived = True

        asyncio.create_task(_send_order_ready(body.order_id))

    return {"status": "accepted"}


@app.post("/webhook/courier-picked-up")
async def webhook_courier_picked_up(body: CourierPickedUpWebhook):
    sim_courier = _require_http_base_url("SIM_COURIER_URL", SIM_COURIER_URL)
    client: httpx.AsyncClient = app.state.http

    async with _state_lock:
        st = _order_state.get(body.order_id)
        if not st:
            raise HTTPException(
                status_code=404, detail="Pedido não encontrado no orquestrador"
            )
        route_to_client = st.route_to_client

    await _request_json(
        client,
        "PATCH",
        f"{ORDER_SERVICE_URL}/pedidos/orders/{body.order_id}/status",
        timeout_s=TIMEOUT_ORDER_S,
        json={"status": "PICKED_UP", "entregador_id": body.courier_id},
    )

    await _request_json(
        client,
        "POST",
        f"{sim_courier}/simulador/entregador/go-to-client",
        timeout_s=TIMEOUT_SIM_S,
        json={
            "order_id": body.order_id,
            "courier_id": body.courier_id,
            "route": route_to_client,
        },
    )

    await _request_json(
        client,
        "PATCH",
        f"{ORDER_SERVICE_URL}/pedidos/orders/{body.order_id}/status",
        timeout_s=TIMEOUT_ORDER_S,
        json={"status": "IN_TRANSIT", "entregador_id": body.courier_id},
    )

    return {"status": "ok"}


@app.post("/webhook/delivered")
async def webhook_delivered(body: DeliveredWebhook):
    client: httpx.AsyncClient = app.state.http

    # 1. Obter a última localização conhecida do entregador no Dynamo (onde ele finalizou a entrega)
    try:
        driver_loc = await _request_json(
            client,
            "GET",
            f"{ORDER_SERVICE_URL}/pedidos/drivers/{body.courier_id}/location",
            timeout_s=TIMEOUT_ORDER_S,
        )
        last_lat, last_lng = _coerce_lat_lon(driver_loc)

        # 2. Persistir essa posição no PostgreSQL para que ele "aguarde" lá
        await _request_json(
            client,
            "PATCH",
            f"{DATABASE_SERVICE_URL}/cadastro/entregadores/{body.courier_id}/localizacao",
            timeout_s=TIMEOUT_DB_S,
            json={"lat": last_lat, "lng": last_lng},
        )
    except Exception as e:
        # Se falhar a sincronização de pós-venda, logamos o erro mas não travamos o fluxo principal
        print(
            f"Erro ao sincronizar localização final do entregador {body.courier_id}: {e}"
        )

    # 3. Finalizar o pedido no serviço de ordens
    await _request_json(
        client,
        "PATCH",
        f"{ORDER_SERVICE_URL}/pedidos/orders/{body.order_id}/status",
        timeout_s=TIMEOUT_ORDER_S,
        json={"status": "DELIVERED", "entregador_id": body.courier_id},
    )

    async with _state_lock:
        _order_state.pop(body.order_id, None)

    return {"status": "ok"}
