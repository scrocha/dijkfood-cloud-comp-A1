from fastapi import FastAPI, HTTPException, Depends
from psycopg2 import pool
import psycopg2
from database.models import Usuario, Restaurante, Entregador, Produto
import os

# credenciais do banco
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "dijkfood")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
SCHEMA = os.getenv("SCHEMA", "dijkfood_schema")

# pool de conexões mín 1 e máx 10
try:
    connection_pool = psycopg2.pool.ThreadedConnectionPool(
        1, 10,
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )
except Exception as e:
    print(f"Erro ao conectar com o banco: {e}")

# inicialização da API
app = FastAPI(title="DijkFood - API de Cadastro", description="Gerencia as Entidades Estáticas")

# dependência para pegar uma conexão do pool e devolver depois que a requisição acabar
def get_db_connection():
    if 'connection_pool' not in globals():
        raise HTTPException(status_code=500, detail="Pool não inicializado. O banco está fora do ar?")
        
    try:
        conn = connection_pool.getconn()
        yield conn
    finally:
        connection_pool.putconn(conn)

# endpoints de cadastro

@app.post("/usuarios", status_code=201)
def cadastrar_usuario(usuario: Usuario, conn = Depends(get_db_connection)):
    """Cadastra um novo usuário no banco de dados [cite: 21]"""

    try:
        cursor = conn.cursor()

        query = f"""
            INSERT INTO {SCHEMA}.USUARIO 
            (USER_ID, PRIMEIRO_NOME, ULTIMO_NOME, EMAIL, TELEFONE, SENHA, DATA_NASCIMENTO, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        valores = (
            usuario.user_id, usuario.primeiro_nome, usuario.ultimo_nome, 
            usuario.email, usuario.telefone, usuario.senha, 
            usuario.data_nascimento, usuario.endereco_latitude, usuario.endereco_longitude
        )
        
        cursor.execute(query, valores)
        conn.commit()
        cursor.close()

        return {"mensagem": "Usuário cadastrado com sucesso!", "id": usuario.user_id}

    except psycopg2.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Usuário já existente ou dados inválidos."
        )
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/restaurantes", status_code=201)
def cadastrar_restaurante(restaurante: Restaurante, conn = Depends(get_db_connection)):
    """Cadastra um novo restaurante no banco de dados [cite: 22]"""

    try:
        cursor = conn.cursor()
        
        query = f"""
            INSERT INTO {SCHEMA}.RESTAURANTE 
            (REST_ID, NOME, TIPO_COZINHA, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
            VALUES (%s, %s, %s, %s, %s)
        """

        valores = (
            restaurante.rest_id, restaurante.nome, restaurante.tipo_cozinha,
            restaurante.endereco_latitude, restaurante.endereco_longitude
        )

        cursor.execute(query, valores)
        conn.commit()
        cursor.close()

        return {"mensagem": "Restaurante cadastrado com sucesso!", "id": restaurante.rest_id}
    
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/entregadores", status_code=201)
def cadastrar_entregador(entregador: Entregador, conn = Depends(get_db_connection)):
    """Cadastra um novo entregador com sua localização inicial [cite: 23]"""

    try:
        cursor = conn.cursor()

        query = f"""
            INSERT INTO {SCHEMA}.ENTREGADOR 
            (ENTREGADOR_ID, NOME, TIPO_VEICULO, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
            VALUES (%s, %s, %s, %s, %s)
        """

        valores = (
            entregador.entregador_id, entregador.nome, entregador.tipo_veiculo,
            entregador.endereco_latitude, entregador.endereco_longitude
        )

        cursor.execute(query, valores)
        conn.commit()
        cursor.close()

        return {"mensagem": "Entregador cadastrado com sucesso!", "id": entregador.entregador_id}
    
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/produtos", status_code=201)
def cadastrar_produto(produto: Produto, conn = Depends(get_db_connection)):
    """Cadastra um novo produto no banco de dados [cite: 24]"""

    try:
        cursor = conn.cursor()

        query = f"""
            INSERT INTO {SCHEMA}.PRODUTOS
            (PROD_ID, NOME, REST_ID) 
            VALUES (%s, %s, %s)
        """

        valores = (
            produto.prod_id, produto.nome, produto.rest_id
        )

        cursor.execute(query, valores)
        conn.commit()
        cursor.close()

        return {"mensagem": "Produto cadastrado com sucesso!", "id": produto.prod_id}
    
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))