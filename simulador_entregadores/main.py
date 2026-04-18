import asyncio
import os
import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException
from contextlib import asynccontextmanager
from typing import Dict

from simulador_entregadores.models import GoToRestaurantRequest, OrderReadyRequest, GoToClientRequest

# Configurações
GENERAL_API_URL = os.getenv("GENERAL_API_URL", "http://general-api:8000").rstrip("/")

# Eventos para sincronizar a espera no restaurante (order_id -> Event)
# Usado para o motoboy ficar parado no restaurante até a comida ficar pronta
_order_ready_events: Dict[str, asyncio.Event] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=10.0)
    yield
    await app.state.http.aclose()

app = FastAPI(title="DijkFood - Worker de Movimentação de Entregadores", lifespan=lifespan)

async def _report_location(courier_id: str, order_id: str, lat: float, lon: float):
    """Envia a posição atual para a API Geral (que salvará no Dynamo)"""
    client: httpx.AsyncClient = app.state.http
    try:
        # A cada passo, a API Geral recebe e salva no DynamoDB
        await client.put(
            f"{GENERAL_API_URL}/tracking/courier/{courier_id}/location",
            json={"lat": lat, "lng": lon, "order_id": order_id}
        )
    except Exception as e:
        print(f"[Worker] Erro ao reportar GPS para {courier_id}: {e}")

async def _executar_trajeto(courier_id: str, order_id: str, route: list):
    """Percorre a rota ponto a ponto simulando o deslocamento real"""
    for point in route:
        lat = point.get("lat")
        lon = point.get("lon") or point.get("lng")
        if lat is not None and lon is not None:
            await _report_location(courier_id, order_id, lat, lon)
            await asyncio.sleep(0.1) # 100ms entre cada 'passo' do GPS (Alta fidelidade para demo)

@app.post("/simulador/entregador/go-to-restaurant")
async def go_to_restaurant(req: GoToRestaurantRequest, background_tasks: BackgroundTasks):
    """
    Fase 1: O motoboy se desloca da sua posição atual (no Dynamo) até o restaurante.
    """
    async def _fase_restaurante():
        print(f"[Worker] Courier {req.courier_id} iniciando deslocamento para Restaurante (Pedido {req.order_id})")
        # 1. Executa a rota até o restaurante
        await _executar_trajeto(req.courier_id, req.order_id, req.route)
        
        # 2. Ao chegar, cria um evento de espera
        print(f"[Worker] Courier {req.courier_id} chegou ao restaurante. Aguardando sinal da cozinha...")
        event = asyncio.Event()
        _order_ready_events[req.order_id] = event
        
        # 3. Fica em espera bloqueante nesta Task
        try:
            await asyncio.wait_for(event.wait(), timeout=600) # 10 min de espera máx.
        except asyncio.TimeoutError:
            print(f"[Worker] Timeout: Motoboy do pedido {req.order_id} cansou de esperar.")
        finally:
            _order_ready_events.pop(req.order_id, None)

    background_tasks.add_task(_fase_restaurante)
    return {"status": "moving_to_restaurant", "courier_id": req.courier_id}

@app.post("/simulador/entregador/order-ready")
async def order_ready(req: OrderReadyRequest):
    """
    Fase 2: Chamado pela API Geral quando o restaurante termina a comida.
    """
    event = _order_ready_events.get(req.order_id)
    if not event:
        # Se não achou o evento, talvez o worker tenha reiniciado. 
        # Como é stateless, o motoboy "perdeu" o contexto, mas a API Geral pode re-enviar.
        raise HTTPException(status_code=404, detail="Pedido não localizado neste worker")
    
    # Acorda o motoboy que estava no wait()
    event.set()
    return {"status": "notified"}

@app.post("/simulador/entregador/go-to-client")
async def go_to_client(req: GoToClientRequest, background_tasks: BackgroundTasks):
    """
    Fase 3: O motoboy recebeu o pacote e agora inicia a rota final até o cliente.
    """
    async def _fase_entrega():
        print(f"[Worker] Courier {req.courier_id} iniciando rota de entrega para o Cliente.")
        # 1. Simula tempo de pickup (1s)
        await asyncio.sleep(1.0)
        
        # 2. Notifica o pickup oficial para mudar status no Dynamo
        client: httpx.AsyncClient = app.state.http
        await client.post(
            f"{GENERAL_API_URL}/webhook/courier-picked-up",
            json={"order_id": req.order_id, "courier_id": req.courier_id}
        )
        
        # 3. Executa a rota até o cliente
        await _executar_trajeto(req.courier_id, req.order_id, req.route)
        
        # 4. Finaliza a entrega
        print(f"[Worker] Pedido {req.order_id} entregue com sucesso!")
        await client.post(
            f"{GENERAL_API_URL}/webhook/delivered",
            json={"order_id": req.order_id, "courier_id": req.courier_id}
        )

    background_tasks.add_task(_fase_entrega)
    return {"status": "moving_to_client"}

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
