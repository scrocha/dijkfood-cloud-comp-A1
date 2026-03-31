import psycopg2
from psycopg2.extras import execute_values
from faker import Faker
import random
import uuid
import osmnx as ox

fake = Faker('pt_BR') # inicializa o faker

NUM_USUARIOS = 1000
NUM_ENTREGADORES = 3000
NUM_RESTAURANTES = 100

DB_HOST = "localhost" # o deploy troca os valores automaticamente
DB_NAME = "dijkfood"
DB_USER = "postgres" # o deploy troca os valores automaticamente
DB_PASS = "postgres" # o deploy troca os valores automaticamente
SCHEMA = "dijkfood_schema"

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

BATCH_SIZE = 10000 # tamanho do batch de inserção

def gerar_coordenadas_validas_sp(quantidade):
    print("Baixando fronteiras de São Paulo...")

    # busca geometria da cidade
    sp_gdf = ox.geocode_to_gdf("São Paulo, São Paulo, Brazil")

    print(f"Gerando {quantidade} pontos aleatórios...")
    # O geopandas tem uma função nativa e ultrarrápida para gerar os pontos no polígono
    amostra = sp_gdf.geometry.sample_points(quantidade)

    # O resultado é uma coleção de pontos (MultiPoint). Vamos extraí-los:
    multiponto = amostra.iloc[0]
    
    # Converte para tuplas (latitude, longitude)
    coordenadas = [(ponto.y, ponto.x) for ponto in multiponto.geoms]
    
    print("Coordenadas geradas com sucesso!")
    return coordenadas

COORDENADAS = iter(gerar_coordenadas_validas_sp(NUM_USUARIOS + NUM_ENTREGADORES + NUM_RESTAURANTES))

def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

def seed_usuarios(cursor, total):
    print(f"Gerando {total} usuários em lotes de {BATCH_SIZE}...")

    query = f"""
        INSERT INTO {SCHEMA}.USUARIO 
        (USER_ID, PRIMEIRO_NOME, ULTIMO_NOME, EMAIL, TELEFONE, SENHA, DATA_NASCIMENTO, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
        VALUES %s
    """
    
    for batch_start in range(0, total, BATCH_SIZE):
        batch_data = []

        for _ in range(min(BATCH_SIZE, total - batch_start)):
            lat, lon = next(COORDENADAS)

            batch_data.append((
                str(uuid.uuid4()),
                fake.first_name(),
                fake.last_name(),
                fake.unique.email(),
                fake.phone_number()[:20],
                fake.password(),
                fake.date_of_birth(minimum_age=18, maximum_age=80),
                lat,
                lon
            ))

        execute_values(cursor, query, batch_data)
        print(f"  -> Inseridos {batch_start + len(batch_data)}/{total}")

def seed_restaurantes(cursor, total):
    print(f"Gerando {total} restaurantes em lotes de {BATCH_SIZE}...")

    query = f"""
        INSERT INTO {SCHEMA}.RESTAURANTE 
        (REST_ID, NOME, TIPO_COZINHA, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
        VALUES %s
    """

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

            batch_data.append((
                novo_rest_id,
                fake.company(),
                cozinha,
                lat,
                lon
            ))

        execute_values(cursor, query, batch_data)
        print(f"  -> Inseridos {batch_start + len(batch_data)}/{total}")

    return rest_ids, cozinhas

def seed_entregadores(cursor, total):
    print(f"Gerando {total} entregadores em lotes de {BATCH_SIZE}...")

    query = f"""
        INSERT INTO {SCHEMA}.ENTREGADOR 
        (ENTREGADOR_ID, NOME, TIPO_VEICULO, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
        VALUES %s
    """
    
    for batch_start in range(0, total, BATCH_SIZE):
        batch_data = []

        for _ in range(min(BATCH_SIZE, total - batch_start)):
            lat, lon = next(COORDENADAS)

            batch_data.append((
                str(uuid.uuid4()),
                fake.name(),
                random.choice(TIPOS_VEICULO),
                lat,
                lon
            ))

        execute_values(cursor, query, batch_data)
        print(f"  -> Inseridos {batch_start + len(batch_data)}/{total}")

def seed_produtos(cursor, rest_ids, cozinhas):
    print(f"Gerando produtos para {len(rest_ids)} restaurantes...")
    
    query = f"""
        INSERT INTO {SCHEMA}.PRODUTOS 
        (PROD_ID, NOME, REST_ID) 
        VALUES %s
    """
    
    batch_data = []
    total_inseridos = 0

    for rest_id, cozinha in zip(rest_ids, cozinhas):
        num_produtos = random.randint(2, 5) # quantos produtos cada restaurante terá
        produtos_escolhidos = random.sample(CARDAPIO[cozinha], k=num_produtos) # sorteia os produtos
        
        for nome_produto in produtos_escolhidos:
            batch_data.append((
                str(uuid.uuid4()),
                nome_produto,
                rest_id
            ))
            
        if len(batch_data) >= BATCH_SIZE:
            execute_values(cursor, query, batch_data)
            total_inseridos += len(batch_data)
            batch_data = []

    if batch_data:
        execute_values(cursor, query, batch_data)
        total_inseridos += len(batch_data)

    print(f"  -> Total de produtos inseridos: {total_inseridos}")

def main():
    conn = get_connection()
    conn.autocommit = False # transação manual para maior segurança e velocidade
    cursor = conn.cursor()

    try:
        # quantidades solicitadas
        rest_ids, cozinhas = seed_restaurantes(cursor, NUM_RESTAURANTES)
        seed_usuarios(cursor, NUM_USUARIOS)
        seed_entregadores(cursor, NUM_ENTREGADORES)
        seed_produtos(cursor, rest_ids, cozinhas)
        
        # confirma todas as inserções
        conn.commit()
        print("\nCarga inicial do banco de dados concluída com sucesso!")
        
    except Exception as e:
        conn.rollback()
        print(f"\nErro durante a inserção: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()