import asyncio
import httpx
import time
import random
import uuid
from faker import Faker
import os

# Ajuste as taxas aqui para bater o número que desejar por segundo.
# Atualmente somando tudo (50+50+50+(50*3)) = 300 requisições HTTP por segundo!
USUARIOS_POR_SEGUNDO = 50
ENTREGADORES_POR_SEGUNDO = 50
RESTAURANTES_POR_SEGUNDO = 50
PRODUTOS_POR_RESTAURANTE = 3

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
        # Timeout reduzido: se a API engasgar, queremos ver o erro rápido
        resp = await client.post(f"{API_URL}{endpoint}", json=data, timeout=5.0)
        lat = time.time() - start
        todas_latencias.append(lat)
        
        if resp.status_code in [200, 201]:
            itens_inseridos += 1 # Agora cada chamada é apenas 1 item
            
        total_reqs_http += 1
    except Exception as e:
        lat = time.time() - start
        todas_latencias.append(lat)
        total_reqs_http += 1
        # Comentado para não poluir o terminal durante o bombardeio, 
        # mas você pode descomentar se quiser ver os timeouts
        # print(f"Erro na requisição {endpoint}: {e}")

async def simular_ciclo(client):
    tasks = []
    restaurantes_criados = []

    # 1. Enfileira as tarefas de Usuários
    for _ in range(USUARIOS_POR_SEGUNDO):
        user = {
            "user_id": str(uuid.uuid4()), 
            "primeiro_nome": fake.first_name(),
            "ultimo_nome": fake.last_name(), 
            "email": fake.unique.email(),
            "telefone": fake.phone_number()[:20], 
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX)
        }
        # ATENÇÃO: Ajuste a rota para a sua rota de criação INDIVIDUAL de usuário
        tasks.append(call_api(client, "/cadastro", user)) 

    # 2. Enfileira as tarefas de Entregadores
    for _ in range(ENTREGADORES_POR_SEGUNDO):
        entregador = {
            "entregador_id": str(uuid.uuid4()), 
            "nome": fake.name(),
            "tipo_veiculo": random.choice(TIPOS_VEICULO),
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX)
        }
        # ATENÇÃO: Ajuste a rota se necessário
        tasks.append(call_api(client, "/cadastro/entregadores", entregador))

    # 3. Enfileira as tarefas de Restaurantes
    for _ in range(RESTAURANTES_POR_SEGUNDO):
        rest_id = str(uuid.uuid4())
        cozinha = random.choice(TIPOS_COZINHA)
        restaurantes_criados.append((rest_id, cozinha))
        restaurante = {
            "rest_id": rest_id, 
            "nome": fake.company(),
            "tipo_cozinha": cozinha, 
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX)
        }
        # ATENÇÃO: Ajuste a rota se necessário
        tasks.append(call_api(client, "/cadastro/restaurantes", restaurante))

    # 4. Dispara todas as requisições de uma vez (Assíncrono)
    await asyncio.gather(*tasks)

    # 5. Prepara e dispara os Produtos (depende dos restaurantes criados)
    tasks_produtos = []
    for rest_id, cozinha in restaurantes_criados:
        pratos_disponiveis = CARDAPIO.get(cozinha, CARDAPIO["Brasileira"])
        escolhidos = random.sample(pratos_disponiveis, k=PRODUTOS_POR_RESTAURANTE)
        for p_nome in escolhidos:
            prod = {"prod_id": str(uuid.uuid4()), "nome": p_nome, "rest_id": rest_id}
            # ATENÇÃO: Ajuste a rota se necessário
            tasks_produtos.append(call_api(client, "/cadastro/produtos", prod))

    if tasks_produtos:
        await asyncio.gather(*tasks_produtos)

async def workload_shooter(client):
    """
    Atirador constante.
    Dispara as requisições a cada 1 segundo.
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
            print(f"[MÉTRICAS] Reqs HTTP (Total): {total_reqs_http} | Inseridos: {itens_inseridos} | P95 Global: {p95:.2f}ms")
        else:
            print("[MÉTRICAS] Aguardando requisições...")

async def main():
    print("Simulador Carga OPEN WORKLOAD ativado. Roteamento INDIVIDUAL (Sem Batch).")
    
    # Aumentamos o limite de conexões para aguentar o tranco no seu PC local
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