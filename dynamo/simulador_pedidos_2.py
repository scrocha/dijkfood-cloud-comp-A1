import asyncio
import httpx
import time
import random
import uuid
import os
import json
from pathlib import Path

# =========================================================================
# CONFIGURAÇÕES DE CARGA (Open Workload)
# =========================================================================
# Dispara N tarefas a cada 1 segundo sem esperar o término do ciclo anterior.
PEDIDOS_POR_SEGUNDO = 15
LOCALIZACOES_POR_SEGUNDO = 40
CONSULTAS_POR_SEGUNDO = 25

# =========================================================================
# DESCOBERTA DE API_URL
# =========================================================================
json_path = Path(__file__).resolve().parent.parent / "deploy_output.json"

if json_path.exists():
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            API_URL = config.get("API_URL")
            print(f"API_URL carregada do JSON: {API_URL}")
    except Exception as e:
        print(f"Erro ao ler JSON, usando fallback: {e}")
        API_URL = os.getenv("API_URL", "http://localhost:8000")
else:
    API_URL = os.getenv("API_URL", "http://localhost:8000")
    print(f"JSON não encontrado, usando API_URL: {API_URL}")

LAT_MIN, LAT_MAX = -23.700, -23.400
LON_MIN, LON_MAX = -46.800, -46.400

# =========================================================================
# MÉTRICAS GLOBAIS
# =========================================================================
todas_latencias = []
itens_inseridos = 0
total_reqs_http = 0
total_erros = 0

# Fila para armazenar order_ids criados e permitir consultas posteriores
pedidos_ativos = asyncio.Queue(maxsize=5000)

async def call_api(client, method, endpoint, data=None):
    global itens_inseridos, total_reqs_http, todas_latencias, total_erros
    start = time.time()
    try:
        if method == "POST":
            resp = await client.post(f"{API_URL}{endpoint}", json=data, timeout=5.0)
        elif method == "PUT":
            resp = await client.put(f"{API_URL}{endpoint}", json=data, timeout=5.0)
        elif method == "GET":
            resp = await client.get(f"{API_URL}{endpoint}", timeout=5.0)
        else:
            return None

        lat = time.time() - start
        todas_latencias.append(lat)
        total_reqs_http += 1
        
        if resp.status_code in [200, 201]:
            itens_inseridos += 1
            return resp.json()
        else:
            total_erros += 1
            return None
    except Exception:
        lat = time.time() - start
        todas_latencias.append(lat)
        total_reqs_http += 1
        total_erros += 1
        return None

# =========================================================================
# AÇÕES SIMULADAS (Baseadas em dynamo/simulador_pedidos.py)
# =========================================================================

async def simular_criacao_pedido(client):
    order_data = {
        "customer_id": str(uuid.uuid4()),
        "restaurant_id": str(uuid.uuid4()),
        "items": [
            {
                "prod_id": str(uuid.uuid4()),
                "nome": f"Produto {random.randint(1, 100)}",
                "preco": round(random.uniform(10, 100), 2)
            }
        ],
        "total_value": round(random.uniform(10, 100), 2)
    }
    res = await call_api(client, "POST", "/pedidos/orders", order_data)
    if res and "order_id" in res:
        try:
            pedidos_ativos.put_nowait(res["order_id"])
        except asyncio.QueueFull:
            try:
                pedidos_ativos.get_nowait()
                pedidos_ativos.put_nowait(res["order_id"])
            except Exception:
                pass

async def simular_update_localizacao(client):
    driver_id = str(uuid.uuid4())
    data = {
        "lat": random.uniform(LAT_MIN, LAT_MAX),
        "lng": random.uniform(LON_MIN, LON_MAX),
        "order_id": str(uuid.uuid4())
    }
    await call_api(client, "PUT", f"/pedidos/drivers/{driver_id}/location", data)

async def simular_consulta_pedido(client):
    if pedidos_ativos.empty():
        return
    try:
        order_id = await pedidos_ativos.get()
        await call_api(client, "GET", f"/pedidos/orders/{order_id}")
        # Reinsere na fila para manter o pool de consultas
        pedidos_ativos.put_nowait(order_id)
    except Exception:
        pass

# =========================================================================
# CICLO E SHOOTER
# =========================================================================

async def simular_ciclo(client):
    tasks = []
    
    # Adiciona tarefas de criação de pedidos
    for _ in range(PEDIDOS_POR_SEGUNDO):
        tasks.append(simular_criacao_pedido(client))
    
    # Adiciona tarefas de localização
    for _ in range(LOCALIZACOES_POR_SEGUNDO):
        tasks.append(simular_update_localizacao(client))
        
    # Adiciona tarefas de consulta
    for _ in range(CONSULTAS_POR_SEGUNDO):
        tasks.append(simular_consulta_pedido(client))
        
    if tasks:
        await asyncio.gather(*tasks)

async def workload_shooter(client):
    """Atirador constante (Open Workload)."""
    while True:
        asyncio.create_task(simular_ciclo(client))
        await asyncio.sleep(1.0)

async def display_metrics():
    """Mostra métricas formatadas a cada 2 segundos."""
    while True:
        await asyncio.sleep(2.0)
        
        global todas_latencias
        # Mantém apenas as últimas 50k latências para o cálculo
        if len(todas_latencias) > 50000:
            todas_latencias = todas_latencias[-50000:]
            
        if len(todas_latencias) > 0:
            sorted_lat = sorted(todas_latencias)
            p95 = sorted_lat[int(len(sorted_lat) * 0.95)] * 1000
            avg_lat = (sum(todas_latencias) / len(todas_latencias)) * 1000
            
            print(f"[MÉTRICAS] Reqs: {total_reqs_http} | Sucs: {itens_inseridos} | Err: {total_erros} | P95: {p95:.1f}ms | Avg: {avg_lat:.1f}ms | Pool: {pedidos_ativos.qsize()}")
        else:
            print("[MÉTRICAS] Aguardando requisições...")

# =========================================================================
# MAIN
# =========================================================================

async def main():
    print("=" * 60)
    print("SIMULADOR PEDIDOS 2 - OPEN WORKLOAD")
    print(f"API Alvo: {API_URL}")
    print(f"Config: {PEDIDOS_POR_SEGUNDO} ped/s | {LOCALIZACOES_POR_SEGUNDO} loc/s | {CONSULTAS_POR_SEGUNDO} cons/s")
    print("=" * 60)
    
    limits = httpx.Limits(max_connections=2000, max_keepalive_connections=1000)
    async with httpx.AsyncClient(limits=limits) as client:
        await asyncio.gather(
            workload_shooter(client),
            display_metrics()
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSimulador encerrado.")
