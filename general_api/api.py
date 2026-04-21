from __future__ import annotations

import asyncio
import datetime
import logging
import math
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- Modelos de Dados ---


class UserCreate(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    lat: float
    lng: float


class RestaurantCreate(BaseModel):
    name: str
    cuisine_type: str
    lat: float
    lng: float


class CourierCreate(BaseModel):
    name: str
    vehicle_type: str
    lat: float
    lng: float


class CheckoutRequest(BaseModel):
    customer_id: str
    restaurant_id: str
    items: list[dict]
    total_value: float


class RestaurantReadyWebhook(BaseModel):
    order_id: str
    restaurant_id: str
    driver_id: str
    route_to_client: list[dict]


class CourierLocationUpdate(BaseModel):
    lat: float
    lng: float
    order_id: Optional[str] = None


class DeliveredWebhook(BaseModel):
    order_id: str
    courier_id: str


class CourierAtRestaurantWebhook(BaseModel):
    order_id: str
    courier_id: str


ACTIVE_ORDER_STATUSES = [
    "CONFIRMED",
    "PREPARING",
    "READY_FOR_PICKUP",
    "PICKED_UP",
    "IN_TRANSIT",
]


# --- Configurações e Globais ---

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

TIMEOUT_DB_S = float(os.getenv("TIMEOUT_DB_S", "10"))
TIMEOUT_ORDER_S = float(os.getenv("TIMEOUT_ORDER_S", "10"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("general_api")

# Caches para performance
CACHE_USUARIOS = {}
CACHE_RESTAURANTES = {}

# --- Utilitários ---


def _require_http_base_url(env_name: str, value: Optional[str]) -> str:
    if not value:
        raise HTTPException(
            status_code=424, detail=f"Configuracao ausente: {env_name}"
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


def _coerce_lat_lon(obj: dict[str, Any]) -> tuple[float, float]:
    lat = obj.get("lat") or obj.get("latitude") or obj.get("endereco_latitude")
    lon = (
        obj.get("lon")
        or obj.get("lng")
        or obj.get("longitude")
        or obj.get("endereco_longitude")
    )
    if lat is None or lon is None:
        raise HTTPException(status_code=502, detail="Coords ausentes")
    return float(lat), float(lon)


def _coerce_driver_id(obj: dict[str, Any]) -> str:
    for k in ("driver_id", "courier_id", "entregador_id", "id"):
        v = obj.get(k)
        if v:
            return str(v)
    raise HTTPException(status_code=502, detail="ID do driver ausente")


def _interpolate_route(lat1, lon1, lat2, lon2, num_points=5):
    return [
        {
            "lat": lat1 + (lat2 - lat1) * (i / num_points),
            "lon": lon1 + (lon2 - lon1) * (i / num_points),
        }
        for i in range(num_points + 1)
    ]


def _service_label_from_url(url: str) -> str:
    if url.startswith(DATABASE_SERVICE_URL):
        return "database"
    if url.startswith(ORDER_SERVICE_URL):
        return "order"
    if SIM_RESTAURANT_URL and url.startswith(SIM_RESTAURANT_URL.rstrip("/")):
        return "sim_restaurante"
    if SIM_COURIER_URL and url.startswith(SIM_COURIER_URL.rstrip("/")):
        return "sim_entregadores"
    if ROUTE_SERVICE_URL and url.startswith(ROUTE_SERVICE_URL):
        return "route"
    return "external"


def _log_checkout_step(
    trace_id: str,
    step: str,
    started: float,
    status: str = "ok",
    detail: str = "",
):
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "checkout_step trace=%s step=%s status=%s duration_ms=%.2f detail=%s",
        trace_id,
        step,
        status,
        elapsed_ms,
        detail,
    )


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    timeout_s: float,
    json: Any | None = None,
) -> Any:
    started = time.perf_counter()
    try:
        resp = await client.request(method, url, json=json, timeout=timeout_s)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "downstream_call service=%s method=%s url=%s status=%s duration_ms=%.2f",
            _service_label_from_url(url),
            method,
            url,
            resp.status_code,
            elapsed_ms,
        )
        if resp.status_code >= 400:
            print(
                f"[Gateway] Erro {resp.status_code} em {url}: {resp.text[:100]}"
            )
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json() if resp.content else None
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "downstream_call service=%s method=%s url=%s status=error duration_ms=%.2f error=%s",
            _service_label_from_url(url),
            method,
            url,
            elapsed_ms,
            str(e),
        )
        print(f"[Gateway] Falha na requisição {url}: {str(e)}")
        raise HTTPException(status_code=502, detail=str(e))


# --- Loop de Auto-Cura (Drainer) ---


async def _drain_loop(app: FastAPI):
    await asyncio.sleep(20)
    print("[Drainer] Iniciando busca de pedidos estagnados")
    while True:
        try:
            client = app.state.http
            sim_rest = (
                SIM_RESTAURANT_URL.rstrip("/") if SIM_RESTAURANT_URL else None
            )
            sim_cour = SIM_COURIER_URL.rstrip("/") if SIM_COURIER_URL else None
            if not sim_rest or not sim_cour:
                await asyncio.sleep(15)
                continue

            # Busca pedidos parados
            to_fix = []
            for s in ["CONFIRMED", "PREPARING", "READY_FOR_PICKUP"]:
                try:
                    res = await _request_json(
                        client,
                        "GET",
                        f"{ORDER_SERVICE_URL}/pedidos/orders/status/{s}",
                        timeout_s=5,
                    )
                    if res:
                        to_fix.extend(res)
                except Exception:
                    continue

            now = datetime.datetime.now(datetime.timezone.utc)
            for o in to_fix[:10]:
                oid = o.get("order_id")
                st = o.get("status")
                rid = o.get("restaurant_id")
                cid = o.get("entregador_id")

                # Check idade
                c_at_str = o.get("created_at") or o.get("updated_at")
                if not c_at_str:
                    continue
                c_at = datetime.datetime.fromisoformat(
                    c_at_str.replace("Z", "+00:00")
                )
                age = (now - c_at).total_seconds()

                if st == "CONFIRMED" and age > 60:
                    # Tenta empurrar para PREPARING
                    try:
                        rest_data = await _request_json(
                            client,
                            "GET",
                            f"{DATABASE_SERVICE_URL}/cadastro/restaurantes/{rid}",
                            timeout_s=5,
                        )
                        rlat, rlon = _coerce_lat_lon(rest_data)
                        drivers = await _request_json(
                            client,
                            "GET",
                            f"{ORDER_SERVICE_URL}/pedidos/drivers/status/free?limit=5",
                            timeout_s=5,
                        )
                        if not drivers:
                            continue
                        cid = _coerce_driver_id(drivers[0])
                        await _request_json(
                            client,
                            "PATCH",
                            f"{ORDER_SERVICE_URL}/pedidos/orders/{oid}/status",
                            timeout_s=5,
                            json={"status": "PREPARING", "entregador_id": cid},
                        )
                        st = "PREPARING"
                    except Exception:
                        continue

                if st == "PREPARING" and age > 120:
                    # Re-notifica restaurante
                    try:
                        rest_data = await _request_json(
                            client,
                            "GET",
                            f"{DATABASE_SERVICE_URL}/cadastro/restaurantes/{rid}",
                            timeout_s=5,
                        )
                        rlat, rlon = _coerce_lat_lon(rest_data)
                        route = _interpolate_route(
                            rlat, rlon, rlat + 0.01, rlon + 0.01, 5
                        )
                        await client.post(
                            f"{sim_rest}/simulador/restaurante/prepare",
                            json={
                                "order_id": oid,
                                "restaurant_id": rid,
                                "driver_id": cid,
                                "route_to_client": route,
                            },
                            timeout=5.0,
                        )
                    except Exception:
                        continue

        except Exception:
            pass
        await asyncio.sleep(15)


# --- App Lifecycle ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=100)
    app.state.http = httpx.AsyncClient(limits=limits, timeout=30.0)
    task = asyncio.create_task(_drain_loop(app))
    yield
    task.cancel()
    await app.state.http.aclose()


app = FastAPI(title="DijkFood - API Geral", lifespan=lifespan)


@app.middleware("http")
async def log_request_timing(request: Request, call_next):
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        logger.info(
            "api_request method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            route_path,
            status_code,
            elapsed_ms,
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Endpoints ---


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/usuarios")
async def listar_usuarios():
    return await _request_json(
        app.state.http,
        "GET",
        f"{DATABASE_SERVICE_URL}/cadastro/usuarios",
        timeout_s=5,
    )


@app.get("/restaurantes")
async def listar_restaurantes(page: int = 1, page_size: int = 20):
    items = await _request_json(
        app.state.http,
        "GET",
        f"{DATABASE_SERVICE_URL}/cadastro/restaurantes",
        timeout_s=5,
    )
    start = (page - 1) * page_size
    return {"items": items[start : start + page_size], "total": len(items)}


@app.get("/entregadores/livres")
async def listar_entregadores_livres():
    return await _request_json(
        app.state.http,
        "GET",
        f"{ORDER_SERVICE_URL}/pedidos/drivers/status/free?limit=50",
        timeout_s=5,
    )


@app.post("/checkout", status_code=201)
async def checkout(body: CheckoutRequest):
    sim_rest = _require_http_base_url("SIM_RESTAURANT_URL", SIM_RESTAURANT_URL)
    sim_cour = _require_http_base_url("SIM_COURIER_URL", SIM_COURIER_URL)
    client = app.state.http
    checkout_trace = (
        f"checkout-{int(time.time() * 1000)}-"
        f"{body.customer_id[-6:]}-{body.restaurant_id[-6:]}"
    )

    # Cache de metadados
    fetch_customer_started = time.perf_counter()
    if body.customer_id not in CACHE_USUARIOS:
        CACHE_USUARIOS[body.customer_id] = await _request_json(
            client,
            "GET",
            f"{DATABASE_SERVICE_URL}/cadastro/usuarios/{body.customer_id}",
            timeout_s=5,
        )
    _log_checkout_step(
        checkout_trace, "fetch_customer", fetch_customer_started
    )

    fetch_restaurant_started = time.perf_counter()
    if body.restaurant_id not in CACHE_RESTAURANTES:
        CACHE_RESTAURANTES[body.restaurant_id] = await _request_json(
            client,
            "GET",
            f"{DATABASE_SERVICE_URL}/cadastro/restaurantes/{body.restaurant_id}",
            timeout_s=5,
        )
    _log_checkout_step(
        checkout_trace, "fetch_restaurant", fetch_restaurant_started
    )

    u_lat, u_lon = _coerce_lat_lon(CACHE_USUARIOS[body.customer_id])
    r_lat, r_lon = _coerce_lat_lon(CACHE_RESTAURANTES[body.restaurant_id])

    # Criar pedido
    create_order_started = time.perf_counter()
    pedido = await _request_json(
        client,
        "POST",
        f"{ORDER_SERVICE_URL}/pedidos/orders",
        timeout_s=5,
        json={
            "customer_id": body.customer_id,
            "restaurant_id": body.restaurant_id,
            "items": body.items,
            "total_value": body.total_value,
        },
    )
    order_id = pedido["order_id"]
    _log_checkout_step(
        checkout_trace,
        "create_order",
        create_order_started,
        detail=f"order_id={order_id}",
    )

    # Atribuir entregador a partir do cache compartilhado do serviço de pedidos
    fetch_drivers_started = time.perf_counter()
    drivers = await _request_json(
        client,
        "GET",
        f"{ORDER_SERVICE_URL}/pedidos/drivers/status/free?limit=50",
        timeout_s=5,
    )
    _log_checkout_step(
        checkout_trace,
        "fetch_free_drivers",
        fetch_drivers_started,
        detail=f"count={len(drivers) if isinstance(drivers, list) else 0}",
    )

    if not drivers:
        raise HTTPException(status_code=503, detail="Sem entregadores")

    candidates = []
    invalid_drivers = 0
    for d in drivers:
        try:
            dlat, dlon = _coerce_lat_lon(d)
            driver_id = _coerce_driver_id(d)
        except HTTPException:
            invalid_drivers += 1
            continue

        dist = _haversine_m(r_lat, r_lon, dlat, dlon)
        candidates.append(
            {
                "id": driver_id,
                "lat": dlat,
                "lon": dlon,
                "dist": dist,
            }
        )

    if invalid_drivers:
        logger.info(
            "checkout_candidates trace=%s valid=%s invalid=%s",
            checkout_trace,
            len(candidates),
            invalid_drivers,
        )

    candidates.sort(key=lambda x: x["dist"])
    if not candidates:
        raise HTTPException(
            status_code=503, detail="Sem entregadores candidatos"
        )

    candidate_pool = candidates[: min(len(candidates), 25)]
    random.Random(checkout_trace).shuffle(candidate_pool)

    chosen = None
    assign_started = time.perf_counter()
    attempt_started = assign_started
    last_error = None
    for candidate in candidate_pool[:15]:
        try:
            await _request_json(
                client,
                "PATCH",
                f"{ORDER_SERVICE_URL}/pedidos/orders/{order_id}/status",
                timeout_s=5,
                json={"status": "PREPARING", "entregador_id": candidate["id"]},
            )
            chosen = candidate
            _log_checkout_step(
                checkout_trace,
                "assign_driver",
                assign_started,
                detail=f"driver_id={candidate['id']}",
            )
            break
        except HTTPException as exc:
            last_error = exc
            _log_checkout_step(
                checkout_trace,
                "assign_driver",
                attempt_started,
                status="retry",
                detail=f"driver_id={candidate['id']} status={exc.status_code}",
            )
            attempt_started = time.perf_counter()
            continue

    if not chosen:
        _log_checkout_step(
            checkout_trace,
            "assign_driver",
            assign_started,
            status="error",
            detail=(
                f"attempts={len(candidate_pool[:15])} "
                f"last_error={last_error.status_code if isinstance(last_error, HTTPException) else 'unknown'}"
            ),
        )
        raise HTTPException(
            status_code=503,
            detail=f"Sem entregadores disponíveis ({last_error.detail if isinstance(last_error, HTTPException) else 'indisponível'})",
        )

    # Notifica simuladores (async)
    notify_started = time.perf_counter()

    async def notify():
        try:
            route_to_client = _interpolate_route(r_lat, r_lon, u_lat, u_lon, 3)
            await client.post(
                f"{sim_rest}/simulador/restaurante/prepare",
                json={
                    "order_id": order_id,
                    "restaurant_id": body.restaurant_id,
                    "driver_id": chosen["id"],
                    "route_to_client": route_to_client,
                },
                timeout=5.0,
            )
            route_to_rest = _interpolate_route(
                chosen["lat"], chosen["lon"], r_lat, r_lon, 2
            )
            await client.post(
                f"{sim_cour}/simulador/entregador/go-to-restaurant",
                json={
                    "order_id": order_id,
                    "courier_id": chosen["id"],
                    "route": route_to_rest,
                    "restaurant": {"lat": r_lat, "lon": r_lon},
                    "customer": {"lat": u_lat, "lon": u_lon},
                },
                timeout=5.0,
            )
        except Exception:
            pass

    asyncio.create_task(notify())
    _log_checkout_step(checkout_trace, "notify_simulators", notify_started)
    return {"order_id": order_id, "courier_id": chosen["id"]}


@app.post("/webhook/restaurant-ready")
async def webhook_restaurant_ready(body: RestaurantReadyWebhook):
    sim_cour = _require_http_base_url("SIM_COURIER_URL", SIM_COURIER_URL)
    client = app.state.http
    await _request_json(
        client,
        "PATCH",
        f"{ORDER_SERVICE_URL}/pedidos/orders/{body.order_id}/status",
        timeout_s=5,
        json={"status": "READY_FOR_PICKUP", "entregador_id": body.driver_id},
    )
    await client.post(
        f"{sim_cour}/simulador/entregador/pickup-and-deliver",
        json={
            "order_id": body.order_id,
            "courier_id": body.driver_id,
            "route": body.route_to_client,
        },
        timeout=5.0,
    )
    return {"status": "ok"}


@app.put("/tracking/courier/{courier_id}/location")
async def tracking_update(courier_id: str, body: CourierLocationUpdate):
    async def proxy():
        try:
            await app.state.http.put(
                f"{ORDER_SERVICE_URL}/pedidos/drivers/{courier_id}/location",
                json={
                    "lat": body.lat,
                    "lng": body.lng,
                    "order_id": body.order_id,
                },
                timeout=1.0,
            )
        except Exception:
            pass

    asyncio.create_task(proxy())
    return {"status": "accepted"}


@app.post("/webhook/delivered")
async def webhook_delivered(body: DeliveredWebhook):
    client = app.state.http
    # Loop de seguranca para garantir que todos os status foram passados no Dynamo
    for s in ["PICKED_UP", "IN_TRANSIT", "DELIVERED"]:
        try:
            await _request_json(
                client,
                "PATCH",
                f"{ORDER_SERVICE_URL}/pedidos/orders/{body.order_id}/status",
                timeout_s=5,
                json={"status": s, "entregador_id": body.courier_id},
            )
        except Exception:
            pass
    return {"status": "ok"}


@app.post("/webhook/courier-at-restaurant")
async def courier_at_rest(body: CourierAtRestaurantWebhook):
    return {"status": "ok"}


@app.get("/restaurantes/{rest_id}/itens")
async def listar_itens(rest_id: str):
    return await _request_json(
        app.state.http,
        "GET",
        f"{DATABASE_SERVICE_URL}/cadastro/produtos/restaurante/{rest_id}",
        timeout_s=5,
    )


@app.get("/admin/active-orders")
async def admin_active_orders():
    """Retorna contagem de pedidos ativos (não-DELIVERED) por status.

    Útil para shutdown/drain gracioso dos simuladores (entregadores/restaurante).
    """

    client = app.state.http
    by_status: dict[str, int] = {}
    total = 0
    for status in ACTIVE_ORDER_STATUSES:
        try:
            res = await _request_json(
                client,
                "GET",
                f"{ORDER_SERVICE_URL}/pedidos/orders/status/{status}",
                timeout_s=5,
            )
            count = len(res) if isinstance(res, list) else 0
        except Exception:
            count = 0
        by_status[status] = count
        total += count

    return {"total_active": total, "by_status": by_status}
