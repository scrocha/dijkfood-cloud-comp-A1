import asyncio
import httpx
import time
import random
import uuid
from faker import Faker
import os

RESTAURANTES_POR_SEGUNDO = 3
PRODUTOS_POR_RESTAURANTE = 3
ENTREGADORES_POR_SEGUNDO = 3
USUARIOS_POR_SEGUNDO = 3

# inicializa o Faker
fake = Faker('pt_BR')

# eventualmente vai ser o endereço do cluster
API_URL = os.getenv("API_URL", "http://localhost:8000")

# dados coerentes com o populador
TIPOS_COZINHA = ["Italiana", "Japonesa", "Brasileira", "Hamburgueria", "Mexicana"]

TIPOS_VEICULO = ["Moto", "Bicicleta", "Carro"]

CARDAPIO = {
    "Italiana": ["Pizza Margherita", "Pizza Calabresa", "Lasanha Bolonhesa", "Tiramisu", "Espaguete à Carbonara"],
    "Japonesa": ["Combinado Sushi 20 Peças", "Temaki de Salmão", "Yakisoba de Frango", "Sashimi de Salmão"],
    "Brasileira": ["Feijoada Completa", "Prato Feito", "Porção de Coxinha", "Pudim"],
    "Hamburgueria": ["Hambúrguer Clássico", "Hambúrguer Duplo", "Batata Frita", "Milkshake"],
    "Mexicana": ["Taco de Carne", "Burrito Misto", "Nachos com Guacamole", "Quesadilla"]
}

# TODO - tenho que garantir que isso vai estar dentro de SP
LAT_MIN, LAT_MAX = -23.700, -23.400
LON_MIN, LON_MAX = -46.800, -46.400

# função que faz a requisição
async def call_api(client, endpoint, data):
    start = time.time()
    try:
        resp = await client.post(f"{API_URL}{endpoint}", json=data)
        return resp.status_code, time.time() - start
    except Exception:
        return 500, time.time() - start

# função que simula o ciclo de 18 requisições
async def simular_ciclo(client):
    """
        Gera 18 requisições divididas em 2 micro-lotes para respeitar a Foreign Key

        Como os produtos dependem dos restaurantes, é preciso garantir que os restaurantes 
        sejam criados antes dos produtos. Por isso esses 2 micro-lotes.
    """

    tasks_lote_1 = []
    restaurantes_criados = [] # vamos guardar ID e cozinha
    
    # 3 usuários
    for _ in range(USUARIOS_POR_SEGUNDO):
        user = {
            "user_id": str(uuid.uuid4()),
            "primeiro_nome": fake.first_name(),
            "ultimo_nome": fake.last_name(),
            "email": fake.unique.email(),
            "telefone": fake.phone_number()[:20],
            "senha": fake.password(),
            "data_nascimento": str(fake.date_of_birth(minimum_age=18)),
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX)
        }
        tasks_lote_1.append(call_api(client, "/usuarios", user))

    # 3 entregadores
    for _ in range(ENTREGADORES_POR_SEGUNDO):
        entregador = {
            "entregador_id": str(uuid.uuid4()),
            "nome": fake.name(),
            "tipo_veiculo": random.choice(TIPOS_VEICULO),
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX)
        }
        tasks_lote_1.append(call_api(client, "/entregadores", entregador))

    # 3 restaurantes
    for _ in range(RESTAURANTES_POR_SEGUNDO):
        rest_id = str(uuid.uuid4())
        cozinha = random.choice(TIPOS_COZINHA)
        restaurantes_criados.append((rest_id, cozinha)) # Salva para usar depois
        
        restaurante = {
            "rest_id": rest_id,
            "nome": fake.company(),
            "tipo_cozinha": cozinha,
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX)
        }
        tasks_lote_1.append(call_api(client, "/restaurantes", restaurante))

    # dispara o primeiro lote de 9 requisições
    resultados_lote_1 = await asyncio.gather(*tasks_lote_1)

    # 9 produtos (3 para cada restaurante)
    tasks_lote_2 = []
    for rest_id, cozinha in restaurantes_criados:
        pratos_disponiveis = CARDAPIO.get(cozinha, CARDAPIO["Brasileira"]) # se não tiver na lista, coloca brasileira
        escolhidos = random.sample(pratos_disponiveis, k=PRODUTOS_POR_RESTAURANTE) # pega 3 aleatórios
        
        for p_nome in escolhidos:
            prod = {
                "prod_id": str(uuid.uuid4()), 
                "nome": p_nome, 
                "rest_id": rest_id
            }
            tasks_lote_2.append(call_api(client, "/produtos", prod))
            
    # dispara o segundo lote com as outras 9 requisições
    resultados_lote_2 = await asyncio.gather(*tasks_lote_2)

    # junta os resultados dos dois lotes
    return resultados_lote_1 + resultados_lote_2

# função principal
async def main():
    print("Simulador Carga: 18 reqs/seg (3 Clientes, 3 Entregadores, 3 Restaurantes, 9 Produtos)")
    
    todas_latencias = []
    
    async with httpx.AsyncClient() as client:
        while True:
            start_time = time.time()
            
            # executa o fluxo
            resultados = await simular_ciclo(client)
            
            # análise
            sucessos = sum(1 for status, lat in resultados if status == 201)
            latencias = [lat for status, lat in resultados if lat > 0]
            todas_latencias.extend(latencias)
            
            # calcula o percentil 95 Global
            p95 = sorted(todas_latencias)[int(len(todas_latencias) * 0.95)] * 1000 if todas_latencias else 0
            
            print(f"Lote finalizado | Sucessos: {sucessos}/18 | Latência P95 (Global): {p95:.2f}ms")
            
            # dorme o resto do tempo para cravar 1 segundo por ciclo
            sleep_time = 1.0 - (time.time() - start_time)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSimulador encerrado.")