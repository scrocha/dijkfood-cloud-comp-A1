import asyncio
import os
import random
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI

from simulador_entregadores.models import (
    GoToClientRequest,
    GoToRestaurantRequest,
    OrderReadyRequest,
)

# Configurações
GENERAL_API_URL = os.getenv("GENERAL_API_URL", "http://general-api:8000").rstrip("/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=30.0)
    yield
    await app.state.http.aclose()


app = FastAPI(title="DijkFood - Worker de Movimentação de Entregadores", lifespan=lifespan)


async def _report_location(courier_id: str, order_id: str, lat: float, lon: float):
    """Envia a posição atual para a API Geral (best-effort)."""
    client: httpx.AsyncClient = app.state.http
    try:
        await client.put(
            f"{GENERAL_API_URL}/tracking/courier/{courier_id}/location",
            json={"lat": lat, "lng": lon, "order_id": order_id},
            timeout=5.0,
        )
    except Exception:
        pass  # GPS é best-effort, não travar por isso


async def _executar_trajeto(courier_id: str, order_id: str, route: list):
    """Percorre a rota ponto a ponto simulando o deslocamento."""
    for point in route:
        lat = point.get("lat")
        lon = point.get("lon") or point.get("lng")
        if lat is not None and lon is not None:
            await _report_location(courier_id, order_id, lat, lon)
            await asyncio.sleep(0.3)  # 300ms entre steps (reduz flooding)


async def _webhook_com_retry(endpoint: str, payload: dict, max_retries: int = 3):
    """Envia webhook com retry — essencial para o ciclo de vida funcionar."""
    client: httpx.AsyncClient = app.state.http
    for attempt in range(max_retries):
        try:
            resp = await client.post(
                f"{GENERAL_API_URL}{endpoint}",
                json=payload,
                timeout=15.0,
            )
            if resp.status_code < 500:
                return resp
            print(f"[Worker] Webhook {endpoint} retornou {resp.status_code}, retry {attempt+1}")
        except Exception as e:
            print(f"[Worker] Webhook {endpoint} falhou: {type(e).__name__}, retry {attempt+1}")
        await asyncio.sleep(1.0 * (attempt + 1))
    print(f"[Worker] Webhook {endpoint} falhou após {max_retries} tentativas!")
    return None


@app.post("/simulador/entregador/go-to-restaurant")
async def go_to_restaurant(req: GoToRestaurantRequest, background_tasks: BackgroundTasks):
    async def _fase_restaurante():
        print(f"[Worker] Courier {req.courier_id} indo ao Restaurante (Pedido {req.order_id})")
        await _executar_trajeto(req.courier_id, req.order_id, req.route)
        print(f"[Worker] Courier {req.courier_id} chegou ao restaurante (Pedido {req.order_id})")

        await _webhook_com_retry(
            "/webhook/courier-at-restaurant",
            {"order_id": req.order_id, "courier_id": req.courier_id},
        )

    background_tasks.add_task(_fase_restaurante)
    return {"status": "moving_to_restaurant", "courier_id": req.courier_id}


@app.post("/simulador/entregador/pickup-and-deliver")
async def pickup_and_deliver(req: GoToClientRequest, background_tasks: BackgroundTasks):
    async def _fase_entrega():
        print(f"[Worker] Courier {req.courier_id} coletou Pedido {req.order_id}, indo ao cliente...")
        await asyncio.sleep(random.uniform(0.3, 0.8))

        # Executa a rota até o cliente
        await _executar_trajeto(req.courier_id, req.order_id, req.route)

        # Notifica a entrega (COM RETRY)
        print(f"[Worker] Courier {req.courier_id} entregou Pedido {req.order_id}!")
        await _webhook_com_retry(
            "/webhook/delivered",
            {"order_id": req.order_id, "courier_id": req.courier_id},
        )

    background_tasks.add_task(_fase_entrega)
    return {"status": "picking_up_and_delivering", "courier_id": req.courier_id}


@app.post("/simulador/entregador/go-to-client")
async def go_to_client(req: GoToClientRequest, background_tasks: BackgroundTasks):
    async def _fase_entrega():
        print(f"[Worker] Courier {req.courier_id} entregando ao Cliente.")
        await asyncio.sleep(0.5)
        await _executar_trajeto(req.courier_id, req.order_id, req.route)
        print(f"[Worker] Courier {req.courier_id} entregou Pedido {req.order_id}!")
        await _webhook_com_retry(
            "/webhook/delivered",
            {"order_id": req.order_id, "courier_id": req.courier_id},
        )

    background_tasks.add_task(_fase_entrega)
    return {"status": "moving_to_client", "courier_id": req.courier_id}


@app.post("/simulador/entregador/order-ready")
async def order_ready(req: OrderReadyRequest):
    return {"status": "acknowledged"}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
