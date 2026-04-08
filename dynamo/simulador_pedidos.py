import asyncio
import httpx
import time
import random
import uuid
import os
import boto3
from collections import deque
import json
# =========================================================================
# CONFIGURAÇÕES
# =========================================================================
PEDIDOS_POR_SEGUNDO = 15
LOCALIZACOES_POR_SEGUNDO = 40
CONSULTAS_POR_SEGUNDO = 25

API_URL = os.getenv("API_URL", "http://dijkfood-alb-1175042617.us-east-1.elb.amazonaws.com")
CLUSTER_NAME = "dijkfood-cluster"
SERVICE_NAME = "dijkfood-pedidos-service"
AWS_REGION = "us-east-1"

LAT_MIN, LAT_MAX = -23.700, -23.400
LON_MIN, LON_MAX = -46.800, -46.400

# =========================================================================
# ESTADO GLOBAL DE MÉTRICAS
# =========================================================================
# Janela deslizante: guarda apenas os timestamps e latências dos últimos 10s
# para calcular req/s e latência média recente (não acumulada)
JANELA_SEGUNDOS = 10
historico_reqs = deque()          # (timestamp, latencia_ms) de cada req concluída
historico_erros = deque()         # timestamp de cada erro
pedidos_ativos = asyncio.Queue(maxsize=2000)
num_instancias = 0

ecs = boto3.client("ecs", region_name=AWS_REGION)


# =========================================================================
# MONITORAMENTO DE INSTÂNCIAS ECS
# =========================================================================
async def update_instance_count():
    global num_instancias
    last_count = 0
    while True:
        try:
            response = ecs.list_tasks(
                cluster=CLUSTER_NAME,
                serviceName=SERVICE_NAME,
                desiredStatus='RUNNING'
            )
            count = len(response.get('taskArns', []))
            if count != last_count and last_count != 0:
                direcao = "SCALE OUT +" if count > last_count else "SCALE IN -"
                print(f"\n  [{direcao}] Instâncias: {last_count} → {count}\n")
            last_count = count
            num_instancias = count
        except Exception:
            try:
                response = ecs.list_tasks(cluster=CLUSTER_NAME, family="dijkfood-pedidos-task", desiredStatus='RUNNING')
                num_instancias = len(response.get('taskArns', []))
            except Exception:
                pass
        await asyncio.sleep(5)


# =========================================================================
# CHAMADA HTTP GENÉRICA
# =========================================================================
async def call_api(client, method, endpoint, data=None):
    start = time.monotonic()
    status_ok = False
    try:
        if method == "POST":
            resp = await client.post(f"{API_URL}{endpoint}", json=data, timeout=10.0)
        elif method == "PUT":
            resp = await client.put(f"{API_URL}{endpoint}", json=data, timeout=10.0)
        elif method == "GET":
            resp = await client.get(f"{API_URL}{endpoint}", timeout=10.0)
        else:
            return None

        lat_ms = (time.monotonic() - start) * 1000
        status_ok = resp.status_code in [200, 201]

        now = time.monotonic()
        historico_reqs.append((now, lat_ms, status_ok))

        if status_ok:
            return resp.json()
        return None

    except Exception:
        lat_ms = (time.monotonic() - start) * 1000
        now = time.monotonic()
        historico_reqs.append((now, lat_ms, False))
        return None


# =========================================================================
# AÇÕES SIMULADAS
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
    order_id = await pedidos_ativos.get()
    await call_api(client, "GET", f"/pedidos/orders/{order_id}")
    try:
        pedidos_ativos.put_nowait(order_id)
    except asyncio.QueueFull:
        pass


# =========================================================================
# SHOOTERS: disparam N requisições por segundo e AGUARDAM todas
# =========================================================================
async def shooter_pedidos(client):
    while True:
        tasks = [asyncio.create_task(simular_criacao_pedido(client))
                 for _ in range(PEDIDOS_POR_SEGUNDO)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(1.0)


async def shooter_localizacoes(client):
    while True:
        tasks = [asyncio.create_task(simular_update_localizacao(client))
                 for _ in range(LOCALIZACOES_POR_SEGUNDO)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(1.0)


async def shooter_consultas(client):
    while True:
        tasks = [asyncio.create_task(simular_consulta_pedido(client))
                 for _ in range(CONSULTAS_POR_SEGUNDO)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(1.0)


# =========================================================================
# EXIBIÇÃO DE MÉTRICAS
# =========================================================================
def purge_old(dq, janela_s):
    """Remove entradas mais antigas que janela_s segundos do deque."""
    cutoff = time.monotonic() - janela_s
    while dq and dq[0][0] < cutoff:
        dq.popleft()


async def display_metrics():
    linha = 0
    while True:
        await asyncio.sleep(3.0)

        now = time.monotonic()
        purge_old(historico_reqs, JANELA_SEGUNDOS)

        recent = list(historico_reqs)

        if not recent:
            print(f"[MÉTRICAS] Aguardando requisições...")
            continue

        # req/s na janela deslizante
        reqs_na_janela = len(recent)
        reqs_por_segundo = reqs_na_janela / JANELA_SEGUNDOS

        # latências
        latencias = [r[1] for r in recent]
        lat_media = sum(latencias) / len(latencias)
        lat_sorted = sorted(latencias)
        p95 = lat_sorted[int(len(lat_sorted) * 0.95)]
        p99 = lat_sorted[int(len(lat_sorted) * 0.99)]

        # taxa de erro
        erros = sum(1 for r in recent if not r[2])
        taxa_erro = (erros / reqs_na_janela * 100) if reqs_na_janela else 0

        # cabeçalho a cada 10 linhas
        if linha % 10 == 0:
            print(f"\n{'Instâncias':>10} | {'Req/s':>6} | {'Lat Avg':>9} | {'P95':>9} | {'P99':>9} | {'Erros%':>7}")
            print("-" * 62)

        print(
            f"{num_instancias:>10} | "
            f"{reqs_por_segundo:>6.1f} | "
            f"{lat_media:>7.0f}ms | "
            f"{p95:>7.0f}ms | "
            f"{p99:>7.0f}ms | "
            f"{taxa_erro:>6.1f}%"
        )
        linha += 1


# =========================================================================
# MAIN
# =========================================================================
async def main():
    print("=" * 62)
    print(" SIMULADOR DE CARGA — DijkFood Pedidos (ECS Fargate)")
    print("=" * 62)
    print(f" API:    {API_URL}")
    print(f" Carga:  {PEDIDOS_POR_SEGUNDO} pedidos/s | {LOCALIZACOES_POR_SEGUNDO} locs/s | {CONSULTAS_POR_SEGUNDO} consultas/s")
    print(f" Janela: métricas calculadas nos últimos {JANELA_SEGUNDOS}s ")
    print("=" * 62)

    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=1000, max_keepalive_connections=500)
    ) as client:
        await asyncio.gather(
            update_instance_count(),
            shooter_pedidos(client),
            shooter_localizacoes(client),
            shooter_consultas(client),
            display_metrics()
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSimulador encerrado.")
