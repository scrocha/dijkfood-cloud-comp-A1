import asyncio
import random
import os
import httpx
from fastapi import FastAPI, BackgroundTasks
from contextlib import asynccontextmanager

from simulador_restaurante.models import PrepareOrderRequest

# Configurações
GENERAL_API_URL = os.getenv("GENERAL_API_URL", "http://general-api:8000").rstrip("/")

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=30.0)
    yield
    await app.state.http.aclose()

app = FastAPI(title="DijkFood - Simulador de Restaurante", lifespan=lifespan)

async def _processar_preparo(order_id: str, restaurant_id: str):
    """Simula o tempo de cozinha e notifica a API Geral"""
    # Tempo randômico entre 10 e 15 segundos
    tempo_preparo = random.randint(10, 15)
    print(f"[Cozinha] Restaurante {restaurant_id} iniciando pedido {order_id}. Tempo: {tempo_preparo}s")
    
    await asyncio.sleep(tempo_preparo)
    
    # Notifica que está pronto
    client: httpx.AsyncClient = app.state.http
    try:
        webhook_payload = {
            "order_id": order_id,
            "restaurant_id": restaurant_id
        }
        resp = await client.post(
            f"{GENERAL_API_URL}/webhook/restaurant-ready",
            json=webhook_payload
        )
        if resp.status_code >= 400:
            print(f"[Cozinha] Erro ao enviar webhook ({resp.status_code}): {resp.text}")
        else:
            print(f"[Cozinha] Pedido {order_id} PRONTO e notificado!")
    except Exception as e:
        print(f"[Cozinha] Falha na comunicação com API Geral: {e}")

@app.post("/simulador/restaurante/prepare")
async def prepare_order(req: PrepareOrderRequest, background_tasks: BackgroundTasks):
    """Endpoint passivo chamado pela API Geral para iniciar o preparo"""
    background_tasks.add_task(_processar_preparo, req.order_id, req.restaurant_id)
    return {"status": "preparing", "order_id": req.order_id}

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
