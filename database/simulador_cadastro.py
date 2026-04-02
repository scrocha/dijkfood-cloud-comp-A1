import asyncio
import httpx
import time
import random
import uuid
from faker import Faker
import os

# Mantivemos a sua carga total: 35 de cada + 3 produtos por restaurante (105 produtos) = 210 itens/segundo
RESTAURANTES_POR_SEGUNDO = 35
PRODUTOS_POR_RESTAURANTE = 3
ENTREGADORES_POR_SEGUNDO = 35
USUARIOS_POR_SEGUNDO = 35

fake = Faker('pt_BR')
API_URL = os.getenv("API_URL", "http://localhost:8000")

TIPOS_COZINHA = ["Italiana", "Japonesa", "Brasileira", "Hamburgueria", "Mexicana"]
TIPOS_VEICULO = ["Moto", "Bicicleta", "Carro"]

CARDAPIO = {
    "Italiana": ["Pizza Margherita", "Pizza Calabresa", "Lasanha Bolonhesa", "Tiramisu", "Espaguete à Carbonara"],
    "Japonesa": ["Combinado Sushi 20 Peças", "Temaki de Salmão", "Yakisoba de Frango", "Sashimi de Salmão"],
    "Brasileira": ["Feijoada Completa", "Prato Feito", "Porção de Coxinha", "Pudim"],
    "Hamburgueria": ["Hambúrguer Clássico", "Hambúrguer Duplo", "Batata Frita", "Milkshake"],
    "Mexicana": ["Taco de Carne", "Burrito Misto", "Nachos com Guacamole", "Quesadilla"]
}

LAT_MIN, LAT_MAX = -23.700, -23.400
LON_MIN, LON_MAX = -46.800, -46.400

# Metricas globais
todas_latencias = []
itens_inseridos = 0
total_reqs_http = 0

async def call_api(client, endpoint, data):
    global itens_inseridos, total_reqs_http, todas_latencias
    start = time.time()
    try:
        resp = await client.post(f"{API_URL}{endpoint}", json=data, timeout=15.0)
        lat = time.time() - start
        todas_latencias.append(lat)
        
        if resp.status_code == 201:
            # Como enviamos uma lista (batch), somamos a quantidade de itens na lista
            itens_inseridos += len(data) 
        total_reqs_http += 1
    except Exception as e:
        lat = time.time() - start
        todas_latencias.append(lat)
        total_reqs_http += 1
        print(f"Erro na requisição {endpoint}: {e}")

async def simular_ciclo(client):
    usuarios_lote = []
    entregadores_lote = []
    restaurantes_lote = []
    restaurantes_criados = []

    # 1. Prepara os Lotes (Listas de Dicionários)
    for _ in range(USUARIOS_POR_SEGUNDO):
        user = {
            "user_id": str(uuid.uuid4()), "primeiro_nome": fake.first_name(),
            "ultimo_nome": fake.last_name(), "email": fake.unique.email(),
            "telefone": fake.phone_number()[:20], "senha": fake.password(),
            "data_nascimento": str(fake.date_of_birth(minimum_age=18)),
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX)
        }
        usuarios_lote.append(user)

    for _ in range(ENTREGADORES_POR_SEGUNDO):
        entregador = {
            "entregador_id": str(uuid.uuid4()), "nome": fake.name(),
            "tipo_veiculo": random.choice(TIPOS_VEICULO),
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX)
        }
        entregadores_lote.append(entregador)

    for _ in range(RESTAURANTES_POR_SEGUNDO):
        rest_id = str(uuid.uuid4())
        cozinha = random.choice(TIPOS_COZINHA)
        restaurantes_criados.append((rest_id, cozinha))
        restaurante = {
            "rest_id": rest_id, "nome": fake.company(),
            "tipo_cozinha": cozinha, "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX)
        }
        restaurantes_lote.append(restaurante)

    # 2. Dispara os Lotes apontando para as rotas /batch
    # Apenas 3 requisições HTTP vão transportar as centenas de itens
    tasks_lote_1 = [
        call_api(client, "/usuarios/batch", usuarios_lote),
        call_api(client, "/entregadores/batch", entregadores_lote),
        call_api(client, "/restaurantes/batch", restaurantes_lote)
    ]
    await asyncio.gather(*tasks_lote_1)

    # 3. Prepara o lote de Produtos (depende dos restaurantes criados)
    produtos_lote = []
    for rest_id, cozinha in restaurantes_criados:
        pratos_disponiveis = CARDAPIO.get(cozinha, CARDAPIO["Brasileira"])
        escolhidos = random.sample(pratos_disponiveis, k=PRODUTOS_POR_RESTAURANTE)
        for p_nome in escolhidos:
            prod = {"prod_id": str(uuid.uuid4()), "nome": p_nome, "rest_id": rest_id}
            produtos_lote.append(prod)

    # 4. Dispara 1 única requisição HTTP com todos os produtos do ciclo
    if produtos_lote:
        await asyncio.gather(call_api(client, "/produtos/batch", produtos_lote))

async def workload_shooter(client):
    """
    Atirador constante (Open Workload).
    Dispara os pacotes assincronamente a cada 1 segundo sem aguardar que o anterior responda.
    """
    while True:
        asyncio.create_task(simular_ciclo(client))
        await asyncio.sleep(1.0)

async def display_metrics():
    """Mostra as métricas a cada 2 segundos"""
    while True:
        await asyncio.sleep(2.0)
        
        global todas_latencias
        if len(todas_latencias) > 50000:
            todas_latencias = todas_latencias[-50000:]
            
        if len(todas_latencias) > 0:
            p95 = sorted(todas_latencias)[int(len(todas_latencias) * 0.95)] * 1000
            print(f"[MÉTRICAS] Reqs HTTP: {total_reqs_http} | Itens no BD: {itens_inseridos} | P95 Global: {p95:.2f}ms")
        else:
            print("[MÉTRICAS] Aguardando requisições...")

async def main():
    print("Simulador Carga OPEN WORKLOAD ativado. Roteamento via BATCH.")
    
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=1000, max_keepalive_connections=500)) as client:
        await asyncio.gather(
            workload_shooter(client),
            display_metrics()
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSimulador encerrado.")