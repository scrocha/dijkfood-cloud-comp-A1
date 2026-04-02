import asyncio
import httpx
from faker import Faker
import random
import uuid
import osmnx as ox
import os

fake = Faker('pt_BR') # inicializa o faker

NUM_USUARIOS = 50000
NUM_ENTREGADORES = 150000
NUM_RESTAURANTES = 5000

API_URL = os.getenv("API_URL", "http://localhost:8000")

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

    print(f"Gerando {quantidade} pontos aleatórios...")
    amostra = sp_gdf.geometry.sample_points(quantidade)
    multiponto = amostra.iloc[0]
    coordenadas = [(ponto.y, ponto.x) for ponto in multiponto.geoms]
    
    print("Coordenadas geradas com sucesso!")
    return coordenadas

# Variável p/ as coordenadas (será instanciada no main)
COORDENADAS = None

async def send_batch(client, endpoint, payload):
    try:
        resp = await client.post(f"{API_URL}{endpoint}", json=payload, timeout=60.0)
        resp.raise_for_status()
    except Exception as e:
        print(f"Erro ao enviar para {endpoint}: {e}")

async def seed_usuarios(client, total):
    print(f"Gerando {total} usuários em lotes de {BATCH_SIZE} por API...")
    
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
                "senha": fake.password(),
                "data_nascimento": str(fake.date_of_birth(minimum_age=18, maximum_age=80)),
                "endereco_latitude": lat,
                "endereco_longitude": lon
            })

        await send_batch(client, "/usuarios/batch", batch_data)
        print(f"  -> Inseridos {batch_start + len(batch_data)}/{total}")

async def seed_restaurantes(client, total):
    print(f"Gerando {total} restaurantes em lotes de {BATCH_SIZE} por API...")
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

        await send_batch(client, "/restaurantes/batch", batch_data)
        print(f"  -> Inseridos {batch_start + len(batch_data)}/{total}")

    return rest_ids, cozinhas

async def seed_entregadores(client, total):
    print(f"Gerando {total} entregadores em lotes de {BATCH_SIZE} por API...")
    
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

        await send_batch(client, "/entregadores/batch", batch_data)
        print(f"  -> Inseridos {batch_start + len(batch_data)}/{total}")

async def seed_produtos(client, rest_ids, cozinhas):
    print(f"Gerando produtos para {len(rest_ids)} restaurantes via API...")
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
            await send_batch(client, "/produtos/batch", batch_data)
            total_inseridos += len(batch_data)
            batch_data = []

    if batch_data:
        await send_batch(client, "/produtos/batch", batch_data)
        total_inseridos += len(batch_data)

    print(f"  -> Total de produtos inseridos: {total_inseridos}")

async def run_seed():
    global COORDENADAS
    COORDENADAS = iter(gerar_coordenadas_validas_sp(NUM_USUARIOS + NUM_ENTREGADORES + NUM_RESTAURANTES))
    
    async with httpx.AsyncClient() as client:
        rest_ids, cozinhas = await seed_restaurantes(client, NUM_RESTAURANTES)
        await seed_usuarios(client, NUM_USUARIOS)
        await seed_entregadores(client, NUM_ENTREGADORES)
        await seed_produtos(client, rest_ids, cozinhas)
        print("\nCarga inicial do banco via API concluída com sucesso!")

def main():
    asyncio.run(run_seed())

if __name__ == "__main__":
    main()