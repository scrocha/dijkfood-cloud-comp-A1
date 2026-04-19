import asyncio
import os
import random
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI

from simulador_pedidos.models import StartSimulationRequest

# Configurações
GENERAL_API_URL = os.getenv(
    "GENERAL_API_URL", "http://general-api:8000"
).rstrip("/")

# Auto-start: quando deployado no ECS, inicia automaticamente com rate do env var
AUTO_START = os.getenv("AUTO_START", "false").lower() == "true"
DEFAULT_RATE = float(os.getenv("RATE", "50"))


class SimuladorState:
    def __init__(self):
        self.is_running = False
        self.rate = 50.0  # pedidos por minuto
        self.task: Optional[asyncio.Task] = None
        self.http_client: Optional[httpx.AsyncClient] = None


sim_state = SimuladorState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    sim_state.http_client = httpx.AsyncClient(timeout=30.0)
    if AUTO_START:
        sim_state.is_running = True
        sim_state.rate = DEFAULT_RATE
        sim_state.task = asyncio.create_task(_simular_loop())
        print(f"[Simulador] Auto-start ativado: rate={DEFAULT_RATE} pedidos/min")
    yield
    sim_state.is_running = False
    if sim_state.task:
        sim_state.task.cancel()
    await sim_state.http_client.aclose()


app = FastAPI(
    title="DijkFood - Simulador de Pedidos de Clientes", lifespan=lifespan
)


async def _obter_dados_reais():
    """Busca dados na API Geral para popular a simulação"""
    try:
        # Busca Clientes
        resp_usuarios = await sim_state.http_client.get(
            f"{GENERAL_API_URL}/usuarios"
        )
        usuarios = resp_usuarios.json()

        # Busca Restaurantes
        resp_restaurantes = await sim_state.http_client.get(
            f"{GENERAL_API_URL}/restaurantes?page_size=100"
        )
        restaurantes = resp_restaurantes.json().get("items", [])

        return usuarios, restaurantes
    except Exception as e:
        print(f"[Simulador] Erro ao buscar dados iniciais: {e}")
        return [], []


async def _simular_loop():
    print(f"[Simulador] Loop iniciado com rate={sim_state.rate} pedidos/min")

    while sim_state.is_running:
        try:
            usuarios, restaurantes = await _obter_dados_reais()

            if not usuarios or not restaurantes:
                print(
                    "[Simulador] Sem dados suficientes (usuarios/restaurantes). Aguardando..."
                )
                await asyncio.sleep(5)
                continue

            # Escolhe um cliente e um restaurante aleatórios
            usuario = random.choice(usuarios)
            restaurante = random.choice(restaurantes)

            # Busca itens reais do restaurante escolhido
            rest_id = restaurante.get("id") or restaurante.get(
                "restaurante_id"
            )
            resp_itens = await sim_state.http_client.get(
                f"{GENERAL_API_URL}/restaurantes/{rest_id}/itens"
            )
            itens_disponiveis = resp_itens.json()

            if not itens_disponiveis:
                continue

            # Escolhe de 1 a 3 itens aleatórios
            num_itens = random.randint(1, 3)
            itens_escolhidos = random.sample(
                itens_disponiveis, min(num_itens, len(itens_disponiveis))
            )

            payload_items = []
            total_value = 0.0
            for item in itens_escolhidos:
                qtd = random.randint(1, 2)
                # Tenta pegar ID do produto em diferentes formatos de retorno da API
                pid = item.get("id") or item.get("produto_id") or item.get("prod_id")
                preco = float(item.get("preco") or 10.0)

                payload_items.append(
                    {"produto_id": str(pid), "quantidade": qtd}
                )
                total_value += preco * qtd

            # Monta o Checkout
            checkout_data = {
                "customer_id": str(
                    usuario.get("id") or usuario.get("usuario_id")
                ),
                "restaurant_id": str(rest_id),
                "items": payload_items,
                "total_value": round(total_value, 2),
            }

            # Envia para a API Geral
            print(
                f"[Simulador] Enviando pedido: Cliente {checkout_data['customer_id']} -> Rest {rest_id}"
            )
            try:
                resp_checkout = await sim_state.http_client.post(
                    f"{GENERAL_API_URL}/checkout", json=checkout_data
                )
                if resp_checkout.status_code >= 400:
                    print(
                        f"[Simulador] Erro no checkout ({resp_checkout.status_code}): {resp_checkout.text}"
                    )
            except Exception as e:
                print(f"[Simulador] Falha na rede ao enviar checkout: {e}")

            if resp_checkout.status_code >= 400:
                print(
                    f"[Simulador] Erro no Checkout ({resp_checkout.status_code}): {resp_checkout.text}"
                )

        except Exception as e:
            print(f"[Simulador] Erro no loop de simulação: {e}")

        # Calcula intervalo baseado no rate (pedidos por minuto)
        intervalo = 60.0 / sim_state.rate if sim_state.rate > 0 else 10.0
        await asyncio.sleep(intervalo)


@app.post("/simulador/cliente/start")
async def start_simulador(req: StartSimulationRequest):
    if sim_state.is_running:
        sim_state.rate = req.rate
        return {"status": "already running", "new_rate": sim_state.rate}

    sim_state.is_running = True
    sim_state.rate = req.rate
    sim_state.task = asyncio.create_task(_simular_loop())

    return {"status": "started", "rate": sim_state.rate}


@app.post("/simulador/cliente/stop")
async def stop_simulador():
    if not sim_state.is_running:
        return {"status": "not running"}

    sim_state.is_running = False
    if sim_state.task:
        sim_state.task.cancel()
        sim_state.task = None

    return {"status": "stopped"}


@app.get("/simulador/cliente/status")
async def status_simulador():
    return {
        "is_running": sim_state.is_running,
        "rate": sim_state.rate,
        "target_api": GENERAL_API_URL,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8005)
