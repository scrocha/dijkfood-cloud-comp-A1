import asyncio
import os
import random
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from simulador_restaurante.models import PrepareOrderRequest

# Configurações
GENERAL_API_URL = os.getenv("GENERAL_API_URL", "http://general-api:8000").rstrip("/")

active_tasks = set()

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=30.0)
    yield
    if active_tasks:
        print(f"[Cozinha] Aguardando {len(active_tasks)} preparos finalizarem...")
        await asyncio.gather(*active_tasks, return_exceptions=True)
    await app.state.http.aclose()


app = FastAPI(title="DijkFood - Simulador de Restaurante", lifespan=lifespan)

async def _processar_preparo(
    order_id: str,
    restaurant_id: str,
    driver_id: str | None,
    route_to_client: list | None,
):
    """Simula o tempo de cozinha e notifica a API Geral com todos os dados
    necessários para o próximo passo (stateless — o gateway não guarda nada)."""
    tempo_preparo = random.randint(1, 3)
    print(f"[Cozinha] Restaurante {restaurant_id} preparando pedido {order_id}. Tempo: {tempo_preparo}s")

    await asyncio.sleep(tempo_preparo)

    # Notifica que está pronto — ecoa driver_id e route_to_client
    client: httpx.AsyncClient = app.state.http
    try:
        webhook_payload = {
            "order_id": order_id,
            "restaurant_id": restaurant_id,
            "driver_id": driver_id,
            "route_to_client": route_to_client,
        }
        resp = await client.post(
            f"{GENERAL_API_URL}/webhook/restaurant-ready",
            json=webhook_payload,
        )
        if resp.status_code >= 400:
            print(f"[Cozinha] Erro webhook ({resp.status_code}): {resp.text}")
        else:
            print(f"[Cozinha] Pedido {order_id} PRONTO e notificado!")
    except Exception as e:
        print(f"[Cozinha] Falha na comunicação com API Geral: {e}")

@app.post("/simulador/restaurante/prepare")
async def prepare_order(req: PrepareOrderRequest):
    """Endpoint passivo chamado pela API Geral para iniciar o preparo"""
    task = asyncio.create_task(
        _processar_preparo(
            req.order_id,
            req.restaurant_id,
            req.driver_id,
            req.route_to_client,
        )
    )
    active_tasks.add(task)
    task.add_done_callback(active_tasks.discard)
    return {"status": "preparing", "order_id": req.order_id}

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
