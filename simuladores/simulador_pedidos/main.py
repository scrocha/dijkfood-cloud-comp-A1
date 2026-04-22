import asyncio
import os
import random
import time
from collections import deque
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
DEFAULT_RATE = float(os.getenv("RATE", "10"))


class SimuladorState:
    def __init__(self):
        self.is_running = False
        self.rate = 10.0  # pedidos por SEGUNDO
        self.task: Optional[asyncio.Task] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.latencies = deque(maxlen=5000)
        self.total_sent = 0
        self.total_errors = 0
        self.last_metric_time = 0.0


sim_state = SimuladorState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=100)
    sim_state.http_client = httpx.AsyncClient(timeout=30.0, limits=limits)
    if AUTO_START:
        sim_state.is_running = True
        sim_state.rate = DEFAULT_RATE
        sim_state.task = asyncio.create_task(_simular_loop())
        print(f"[Simulador] Auto-start ativado: rate={DEFAULT_RATE} pedidos/segundo")
    yield
    sim_state.is_running = False
    print("[Simulador] Shutdown iniciado, parando geração de novos pedidos")
    if sim_state.task:
        try:
            await asyncio.wait_for(sim_state.task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            sim_state.task.cancel()
    await sim_state.http_client.aclose()
    print("[Simulador] Shutdown finalizado.")


app = FastAPI(
    title="DijkFood - Simulador de Pedidos de Clientes", lifespan=lifespan
)


async def _obter_dados_reais():
    """Busca dados na API Geral para popular a simulação."""
    try:
        # Busca Clientes
        resp_usuarios = await sim_state.http_client.get(
            f"{GENERAL_API_URL}/usuarios"
        )
        if resp_usuarios.status_code != 200:
            print(f"[Simulador] Erro ao buscar usuários: HTTP {resp_usuarios.status_code}")
            return [], []

        usuarios_data = resp_usuarios.json()

        # Validação robusta: garantir que é uma lista
        if isinstance(usuarios_data, dict):
            # Pode ser paginado {"items": [...]} ou erro {"detail": "..."}
            if "items" in usuarios_data:
                usuarios_data = usuarios_data["items"]
            elif "detail" in usuarios_data:
                print(f"[Simulador] API retornou erro em /usuarios: {usuarios_data['detail']}")
                return [], []
            else:
                print(f"[Simulador] /usuarios retornou dict inesperado: {list(usuarios_data.keys())}")
                return [], []

        if not isinstance(usuarios_data, list):
            print(f"[Simulador] /usuarios retornou tipo inesperado: {type(usuarios_data)}")
            return [], []

        # Busca Restaurantes
        resp_restaurantes = await sim_state.http_client.get(
            f"{GENERAL_API_URL}/restaurantes?page_size=100"
        )
        if resp_restaurantes.status_code != 200:
            print(f"[Simulador] Erro ao buscar restaurantes: HTTP {resp_restaurantes.status_code}")
            return [], []

        rest_data = resp_restaurantes.json()

        # Extrai items da paginação
        if isinstance(rest_data, dict):
            restaurantes = rest_data.get("items", [])
        elif isinstance(rest_data, list):
            restaurantes = rest_data
        else:
            print(f"[Simulador] /restaurantes retornou tipo inesperado: {type(rest_data)}")
            return [], []

        if not isinstance(restaurantes, list):
            restaurantes = []

        print(f"[Simulador] Dados carregados: {len(usuarios_data)} usuários, {len(restaurantes)} restaurantes")
        return usuarios_data, restaurantes

    except Exception as e:
        print(f"[Simulador] Erro ao buscar dados iniciais: {repr(e)}")
        return [], []


async def _processar_pedido_aleatorio(usuarios, restaurantes, cache_itens):
    try:
        if not usuarios or not restaurantes:
            return

        usuario = random.choice(usuarios)
        restaurante = random.choice(restaurantes)

        # Extrair IDs com fallback robusto
        rest_id = (
            restaurante.get("id")
            or restaurante.get("restaurante_id")
            or restaurante.get("rest_id")
        )
        if not rest_id:
            print(f"[Simulador] Restaurante sem ID válido: {list(restaurante.keys())}")
            return

        if rest_id not in cache_itens:
            resp_itens = await sim_state.http_client.get(
                f"{GENERAL_API_URL}/restaurantes/{rest_id}/itens"
            )
            if resp_itens.status_code != 200:
                return
            itens_data = resp_itens.json()
            if not isinstance(itens_data, list):
                itens_data = []
            cache_itens[rest_id] = itens_data

        itens_disponiveis = cache_itens.get(rest_id, [])

        if not itens_disponiveis:
            return

        num_itens = random.randint(1, min(3, len(itens_disponiveis)))
        itens_escolhidos = random.sample(itens_disponiveis, num_itens)

        payload_items = []
        total_value = 0.0
        for item in itens_escolhidos:
            qtd = random.randint(1, 2)
            pid = item.get("id") or item.get("produto_id") or item.get("prod_id")
            if not pid:
                continue
            preco = float(item.get("preco") or 10.0)
            payload_items.append({"produto_id": str(pid), "quantidade": qtd})
            total_value += preco * qtd

        if not payload_items:
            return

        customer_id = (
            usuario.get("id")
            or usuario.get("usuario_id")
            or usuario.get("user_id")
        )
        if not customer_id:
            return

        checkout_data = {
            "customer_id": str(customer_id),
            "restaurant_id": str(rest_id),
            "items": payload_items,
            "total_value": round(total_value, 2),
        }

        # Envia para a API Geral e mede latência
        start_t = time.perf_counter()
        resp_checkout = await sim_state.http_client.post(
            f"{GENERAL_API_URL}/checkout", json=checkout_data
        )
        end_t = time.perf_counter()
        latency_ms = (end_t - start_t) * 1000.0
        sim_state.latencies.append(latency_ms)
        sim_state.total_sent += 1

        if resp_checkout.status_code >= 400:
            sim_state.total_errors += 1
            if sim_state.total_errors % 50 == 1:
                print(f"[Simulador] Erro no checkout ({resp_checkout.status_code}): {resp_checkout.text[:200]}")

    except Exception as e:
        sim_state.total_errors += 1
        if sim_state.total_errors % 50 == 1:
            print(f"[Simulador] Exceção no checkout: {repr(e)}")


async def _simular_loop():
    print(f"[Simulador] Loop iniciado com rate={sim_state.rate} pedidos/segundo")

    cache_usuarios = []
    cache_restaurantes = []
    cache_itens = {}
    cache_refresh_at = 0.0

    while sim_state.is_running:
        try:
            # Refresh do cache a cada 60 segundos ou se vazio
            agora = time.time()
            if not cache_usuarios or not cache_restaurantes or agora > cache_refresh_at:
                cache_usuarios, cache_restaurantes = await _obter_dados_reais()
                cache_refresh_at = agora + 60.0
                cache_itens = {}

            if not cache_usuarios or not cache_restaurantes:
                print("[Simulador] Sem dados suficientes. Aguardando 5s...")
                await asyncio.sleep(5)
                continue

            # Despacho Assíncrono (Fire and Forget) para alcançar altas taxas
            asyncio.create_task(
                _processar_pedido_aleatorio(cache_usuarios, cache_restaurantes, cache_itens)
            )

        except Exception as e:
            print(f"[Simulador] Erro no loop de simulação: {repr(e)}")

        # Imprime métricas para o CloudWatch (a cada ~5 segundos)
        agora = time.time()
        if agora - sim_state.last_metric_time >= 5.0 and len(sim_state.latencies) > 0:
            sim_state.last_metric_time = agora
            lat = sorted(sim_state.latencies)
            idx = min(int(0.95 * len(lat)), len(lat) - 1)
            p95 = lat[idx]
            avg = sum(lat) / len(lat)
            print(
                f"[METRICS] P95={p95:.0f}ms Avg={avg:.0f}ms | "
                f"Rate={sim_state.rate}/s | Sent={sim_state.total_sent} Err={sim_state.total_errors}"
            )

        # Calcula intervalo baseado no rate (pedidos por SEGUNDO)
        intervalo = 1.0 / sim_state.rate if sim_state.rate > 0 else 1.0
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
    lat = list(sim_state.latencies)
    p95 = 0.0
    avg = 0.0
    if lat:
        lat.sort()
        idx = min(int(0.95 * len(lat)), len(lat) - 1)
        p95 = lat[idx]
        avg = sum(lat) / len(lat)

    return {
        "is_running": sim_state.is_running,
        "rate": sim_state.rate,
        "target_api": GENERAL_API_URL,
        "p95_latency_ms": round(p95, 2),
        "avg_latency_ms": round(avg, 2),
        "total_requests_tracked": len(lat),
        "total_sent": sim_state.total_sent,
        "total_errors": sim_state.total_errors,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8005)
