import asyncio
import httpx
from faker import Faker
import random
import uuid
import osmnx as ox
import os
import json
from pathlib import Path

fake = Faker('pt_BR')

COORDENADAS = None

NUM_USUARIOS = int(os.environ.get("SEED_USERS", "5000"))
NUM_ENTREGADORES = int(os.environ.get("SEED_DRIVERS", "15000"))
NUM_RESTAURANTES = int(os.environ.get("SEED_RESTAURANTS", "100"))

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

TIPOS_COZINHA = ["Italiana", "Japonesa", "Brasileira", "Hamburgueria", "Mexicana"]
TIPOS_VEICULO = ["Moto", "Bicicleta", "Carro"]
CARDAPIO = {
    "Italiana": [
        "Pizza Margherita", "Pizza Calabresa", "Lasanha Bolonhesa", "Tiramisu",
        "Espaguete à Carbonara", "Nhoque ao Sugo", "Risoto de Funghi", "Ravioli de Queijo",
        "Bruschetta Tradicional", "Carpaccio de Carne", "Pizza Quatro Queijos", 
        "Fettuccine Alfredo", "Penne ao Pesto", "Polenta Frita", "Canelone de Espinafre",
        "Calzone de Frango", "Focaccia de Alecrim", "Panna Cotta", "Ossobuco com Polenta",
        "Salada Caprese"
    ],
    "Japonesa": [
        "Combinado Sushi 20 Peças", "Temaki de Salmão", "Yakisoba de Frango", "Sashimi de Salmão",
        "Sashimi de Atum", "Uramaki Filadélfia", "Hot Roll", "Sunomono", 
        "Missoshiro", "Guioza de Carne", "Tempurá de Legumes", "Shimeji na Manteiga",
        "Hossomaki de Pepino", "Niguiri de Peixe Branco", "Temaki de Atum", "Ceviche Oriental",
        "Teppanyaki de Mignon", "Yakitori de Frango", "Udon de Frutos do Mar", "Mochi de Morango"
    ],
    "Brasileira": [
        "Feijoada Completa", "Prato Feito", "Porção de Coxinha", "Pastel de Carne", 
        "Açaí 500ml", "Pudim", "Moqueca de Camarão", "Pão de Queijo", 
        "Churrasco Misto", "Baião de Dois", "Vatapá", "Acarajé", 
        "Tapioca de Carne Seca", "Escondidinho de Mandioca", "Picanha na Chapa", "Caldo de Cana",
        "Frango com Quiabo", "Bobó de Camarão", "Bolinho de Bacalhau", "Brigadeiro Tradicional"
    ],
    "Hamburgueria": [
        "Hambúrguer Clássico", "Hambúrguer Duplo", "Batata Frita", "Refrigerante Lata", 
        "Suco Natural", "Cheeseburger", "Smash Burger", "Hambúrguer Artesanal de Costela",
        "Hambúrguer Vegetariano", "Onion Rings", "Batata Frita com Cheddar e Bacon", 
        "Milkshake de Morango", "Milkshake de Chocolate", "Hambúrguer de Frango Empanado", 
        "Hambúrguer de Picanha", "Nuggets de Frango", "Porção de Mini Burgers", 
        "Hambúrguer com Gorgonzola", "Hambúrguer de Salmão", "Sundae de Caramelo"
    ],
    "Mexicana": [
        "Taco de Carne", "Burrito Misto", "Nachos com Guacamole", "Quesadilla de Frango",
        "Fajitas de Carne", "Chilli com Carne", "Tacos Al Pastor", "Enchiladas de Queijo",
        "Burrito de Porco", "Porção de Guacamole", "Sour Cream (Creme Azedo)", "Pico de Gallo",
        "Churros com Doce de Leite", "Tostadas de Frango", "Sopa de Tortilla", 
        "Ceviche Mexicano", "Queso Fundido", "Tamales", "Margarita Clássica", "Michelada"
    ]
}

BATCH_SIZE = 500

def gerar_coordenadas_validas_sp(quantidade):
    print("Baixando fronteiras de São Paulo...")
    sp_gdf = ox.geocode_to_gdf("São Paulo, São Paulo, Brazil")

    print(f"Gerando {quantidade} pontos estritamente dentro da área suportada pelas Rotas...")
    
    coordenadas = []
    # Multiplicador alto para garantir que acharemos o suficiente devido à exclusão da zona sul (Parelheiros)
    amostra = sp_gdf.geometry.sample_points(quantidade * 3)
    multiponto = amostra.iloc[0]
    
    for ponto in multiponto.geoms:
        lat, lon = ponto.y, ponto.x
        # Limites cravados do target group API Rotas:
        if (-23.9857223 <= lat <= -23.3590754) and (-46.8253578 <= lon <= -46.3653906):
            coordenadas.append((lat, lon))
            if len(coordenadas) == quantidade:
                break
                
    while len(coordenadas) < quantidade:
        coordenadas.append(coordenadas[0] if coordenadas else (-23.5505, -46.6333))

    print(f"{quantidade} coordenadas válidas de SP selecionadas.")
    return coordenadas

async def esperar_api_pronta(client, url, max_retries=30, delay=10):
    print(f"Aguardando a API ({url}) ficar completamente pronta no Target Group...")
    # Fase 1: espera /cadastro/health responder 200
    for i in range(max_retries):
        try:
            resp = await client.get(f"{url}/cadastro/health", timeout=10.0)
            if resp.status_code == 200:
                print("Health OK! Testando endpoint de dados...")
                break
        except Exception:
            pass
        print(f"API ainda não responde (Tentativa {i+1}/{max_retries}). Aguardando {delay}s...")
        await asyncio.sleep(delay)
    else:
        print("Aviso: Limite de tempo esgotado aguardando API. Tentando prosseguir mesmo assim.")
        return False

    # Fase 2: espera um endpoint real funcionar (502 significa ALB ainda roteando)
    for i in range(15):
        try:
            resp = await client.get(f"{url}/cadastro/restaurantes", timeout=15.0)
            if resp.status_code < 500:
                print("A API está online e pronta para receber as requisições!")
                return True
        except Exception:
            pass
        print(f"Endpoint de dados ainda com 502 (Tentativa {i+1}/15). Aguardando 5s...")
        await asyncio.sleep(5)

    print("Aviso: Endpoints de dados não responderam. Tentando seed mesmo assim.")
    return False

async def send_batch(client, endpoint, payload, retries=5):
    url = f"{API_URL}{endpoint}"
    for i in range(retries):
        try:
            resp = await client.post(url, json=payload, timeout=60.0)
            resp.raise_for_status()
            return
        except Exception as e:
            if i < retries - 1:
                print(f"Aviso: Erro ao enviar para {endpoint}: {e}. Retentando em 5s (Tentativa {i+1})...")
                await asyncio.sleep(5)
            else:
                print(f"Erro definitivo ao enviar para {endpoint} após {retries} tentativas: {e}")

async def seed_usuarios(client, total):
    print(f"Iniciando carga de {total} usuários...")
    
    for batch_start in range(0, total, BATCH_SIZE):
        batch_data = []

        for _ in range(min(BATCH_SIZE, total - batch_start)):
            lat, lon = next(COORDENADAS)
            batch_data.append({
                "user_id": str(uuid.uuid4()),
                "primeiro_nome": fake.first_name(),
                "ultimo_nome": fake.last_name(),
                "email": fake.unique.email(),
                "telefone": fake.phone_number()[:20],
                "endereco_latitude": lat,
                "endereco_longitude": lon
            })

        await send_batch(client, "/cadastro/batch", batch_data)
        
    print(f"Carga de usuários finalizada. {total} usuários inseridos.")

async def seed_restaurantes(client, total):
    print(f"Iniciando carga de {total} restaurantes...")
    rest_ids = []
    cozinhas = []

    for batch_start in range(0, total, BATCH_SIZE):
        batch_data = []

        for _ in range(min(BATCH_SIZE, total - batch_start)):
            lat, lon = next(COORDENADAS)
            novo_rest_id = str(uuid.uuid4())
            rest_ids.append(novo_rest_id)
            cozinha = random.choice(TIPOS_COZINHA)
            cozinhas.append(cozinha)

            batch_data.append({
                "rest_id": novo_rest_id,
                "nome": fake.company(),
                "tipo_cozinha": cozinha,
                "endereco_latitude": lat,
                "endereco_longitude": lon
            })

        await send_batch(client, "/cadastro/restaurantes/batch", batch_data)
        
    print(f"Carga de restaurantes finalizada. {total} restaurantes inseridos.")
    return rest_ids, cozinhas

async def seed_entregadores(client, total):
    print(f"Iniciando carga de {total} entregadores...")
    
    for batch_start in range(0, total, BATCH_SIZE):
        batch_data = []

        for _ in range(min(BATCH_SIZE, total - batch_start)):
            lat, lon = next(COORDENADAS)

            batch_data.append({
                "entregador_id": str(uuid.uuid4()),
                "nome": fake.name(),
                "tipo_veiculo": random.choice(TIPOS_VEICULO),
                "endereco_latitude": lat,
                "endereco_longitude": lon
            })

        # Carga PostgreSQL (API Cadastro)
        await send_batch(client, "/cadastro/entregadores/batch", batch_data)
        
        # Inicializa o 'status' LIVRE do entregador no DynamoDB (API Pedidos/Orquestrador)
        dynamo_payload = [
            {
                "driver_id": item["entregador_id"],
                "lat": item["endereco_latitude"],
                "lng": item["endereco_longitude"],
            }
            for item in batch_data
        ]
        await send_batch(client, "/pedidos/drivers/batch-location", dynamo_payload)
        
    print(f"Carga de entregadores finalizada. {total} entregadores inseridos.")

async def seed_produtos(client, rest_ids, cozinhas):
    print(f"Iniciando carga de produtos para os restaurantes...")
    batch_data = []
    total_inseridos = 0

    for rest_id, cozinha in zip(rest_ids, cozinhas):
        num_produtos = random.randint(2, 5) 
        produtos_escolhidos = random.sample(CARDAPIO[cozinha], k=num_produtos) 
        
        for nome_produto in produtos_escolhidos:
            batch_data.append({
                "prod_id": str(uuid.uuid4()),
                "nome": nome_produto,
                "rest_id": rest_id
            })
            
        if len(batch_data) >= BATCH_SIZE:
            await send_batch(client, "/cadastro/produtos/batch", batch_data)
            total_inseridos += len(batch_data)
            batch_data = []

    if batch_data:
        await send_batch(client, "/cadastro/produtos/batch", batch_data)
        total_inseridos += len(batch_data)

    print(f"Total de produtos inseridos: {total_inseridos}")

async def run_seed():
    global COORDENADAS
    COORDENADAS = iter(gerar_coordenadas_validas_sp(NUM_USUARIOS + NUM_ENTREGADORES + NUM_RESTAURANTES))
    
    async with httpx.AsyncClient() as client:
        await esperar_api_pronta(client, API_URL)
        rest_ids, cozinhas = await seed_restaurantes(client, NUM_RESTAURANTES)
        await seed_usuarios(client, NUM_USUARIOS)
        await seed_entregadores(client, NUM_ENTREGADORES)
        await seed_produtos(client, rest_ids, cozinhas)
        print("Carga inicial do banco concluída!")

def main():
    asyncio.run(run_seed())

if __name__ == "__main__":
    main()