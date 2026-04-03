from fastapi import FastAPI, HTTPException, Depends
from typing import List
import asyncpg
from contextlib import asynccontextmanager
from database.models import Usuario, Restaurante, Entregador, Produto, Pedido
import os

# credenciais do banco
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "dijkfood")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
SCHEMA = os.getenv("SCHEMA", "dijkfood_schema")
DB_MIN_CONN = int(os.getenv("DB_MIN_CONN", 1))
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", 20))


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        app.state.pool = await asyncpg.create_pool(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            min_size=DB_MIN_CONN,
            max_size=DB_MAX_CONN
        )
    except Exception as e:
        print(f"Erro ao conectar com o banco: {e}")
        app.state.pool = None
    yield
    if getattr(app.state, "pool", None):
        await app.state.pool.close()

app = FastAPI(title="DijkFood - API de Cadastro", description="Gerencia as Entidades Estáticas e Pedidos", lifespan=lifespan)

# dependência para pegar uma conexão do pool asyncpg
async def get_db_connection():
    if not getattr(app.state, "pool", None):
        raise HTTPException(status_code=500, detail="Pool não inicializado. O banco está fora do ar?")

    async with app.state.pool.acquire() as conn:
        yield conn

@app.get("/cadastro/health", status_code=200, tags=["Monitoramento"])
async def health_check():
    """Verifica se a API está no ar."""

    return {"status": "ok", "mensagem": "DijkFood API está rodando!"}

# ==================================================
# USUÁRIOS
# ==================================================

@app.post("/cadastro/usuarios", status_code=201)
async def cadastrar_usuario(usuario: Usuario, conn = Depends(get_db_connection)):
    """Cadastra um novo usuário no banco de dados"""

    query = f"""
        INSERT INTO {SCHEMA}.USUARIO 
        (USER_ID, PRIMEIRO_NOME, ULTIMO_NOME, EMAIL, TELEFONE, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    """
    valores = (
        usuario.user_id, usuario.primeiro_nome, usuario.ultimo_nome, 
        usuario.email, usuario.telefone, 
        usuario.endereco_latitude, usuario.endereco_longitude
    )
    try:
        await conn.execute(query, *valores)
        return {"mensagem": "Usuário cadastrado com sucesso!", "id": usuario.user_id}
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Usuário já existente ou dados inválidos.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cadastro/batch", status_code=201)
async def cadastrar_usuarios_batch(usuarios: List[Usuario], conn = Depends(get_db_connection)):
    """Cadastra múltiplos usuários de uma vez (Bulk Insert)"""

    query = f"""
        INSERT INTO {SCHEMA}.USUARIO 
        (USER_ID, PRIMEIRO_NOME, ULTIMO_NOME, EMAIL, TELEFONE, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    """
    valores = [
        (u.user_id, u.primeiro_nome, u.ultimo_nome, u.email, u.telefone, u.endereco_latitude, u.endereco_longitude)
        for u in usuarios
    ]
    try:
        await conn.executemany(query, valores)
        return {"mensagem": f"{len(usuarios)} Usuários cadastrados com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cadastro/usuarios/{user_id}", response_model=Usuario)
async def obter_usuario(user_id: str, conn = Depends(get_db_connection)):
    """Busca um usuário específico pelo ID"""

    query = f"SELECT * FROM {SCHEMA}.USUARIO WHERE USER_ID = $1"
    try:
        registro = await conn.fetchrow(query, user_id)
        if not registro:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        return dict(registro)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================================================
# RESTAURANTES
# ==================================================

@app.post("/cadastro/restaurantes", status_code=201)
async def cadastrar_restaurante(restaurante: Restaurante, conn = Depends(get_db_connection)):
    """Cadastra um novo restaurante no banco de dados"""

    query = f"""
        INSERT INTO {SCHEMA}.RESTAURANTE 
        (REST_ID, NOME, TIPO_COZINHA, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
        VALUES ($1, $2, $3, $4, $5)
    """
    valores = (
        restaurante.rest_id, restaurante.nome, restaurante.tipo_cozinha,
        restaurante.endereco_latitude, restaurante.endereco_longitude
    )
    try:
        await conn.execute(query, *valores)
        return {"mensagem": "Restaurante cadastrado com sucesso!", "id": restaurante.rest_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cadastro/restaurantes/batch", status_code=201)
async def cadastrar_restaurantes_batch(restaurantes: List[Restaurante], conn = Depends(get_db_connection)):
    """Cadastra múltiplos restaurantes de uma vez"""

    query = f"""
        INSERT INTO {SCHEMA}.RESTAURANTE 
        (REST_ID, NOME, TIPO_COZINHA, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
        VALUES ($1, $2, $3, $4, $5)
    """
    valores = [
        (r.rest_id, r.nome, r.tipo_cozinha, r.endereco_latitude, r.endereco_longitude)
        for r in restaurantes
    ]
    try:
        await conn.executemany(query, valores)
        return {"mensagem": f"{len(restaurantes)} Restaurantes cadastrados com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cadastro/restaurantes", response_model=List[Restaurante])
async def listar_restaurantes(conn = Depends(get_db_connection)):
    """Lista todos os restaurantes cadastrados"""

    query = f"SELECT * FROM {SCHEMA}.RESTAURANTE"
    try:
        registros = await conn.fetch(query)
        return [dict(registro) for registro in registros]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cadastro/restaurantes/{rest_id}", response_model=Restaurante)
async def obter_restaurante(rest_id: str, conn = Depends(get_db_connection)):
    """Busca um restaurante específico pelo ID"""

    query = f"SELECT * FROM {SCHEMA}.RESTAURANTE WHERE REST_ID = $1"
    try:
        registro = await conn.fetchrow(query, rest_id)
        if not registro:
            raise HTTPException(status_code=404, detail="Restaurante não encontrado")
        return dict(registro)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================================================
# ENTREGADORES
# ==================================================

@app.post("/cadastro/entregadores", status_code=201)
async def cadastrar_entregador(entregador: Entregador, conn = Depends(get_db_connection)):
    """Cadastra um novo entregador com sua localização inicial"""

    query = f"""
        INSERT INTO {SCHEMA}.ENTREGADOR 
        (ENTREGADOR_ID, NOME, TIPO_VEICULO, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
        VALUES ($1, $2, $3, $4, $5)
    """
    valores = (
        entregador.entregador_id, entregador.nome, entregador.tipo_veiculo,
        entregador.endereco_latitude, entregador.endereco_longitude
    )
    try:
        await conn.execute(query, *valores)
        return {"mensagem": "Entregador cadastrado com sucesso!", "id": entregador.entregador_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cadastro/entregadores/batch", status_code=201)
async def cadastrar_entregadores_batch(entregadores: List[Entregador], conn = Depends(get_db_connection)):
    """Cadastra múltiplos entregadores de uma vez"""

    query = f"""
        INSERT INTO {SCHEMA}.ENTREGADOR 
        (ENTREGADOR_ID, NOME, TIPO_VEICULO, ENDERECO_LATITUDE, ENDERECO_LONGITUDE) 
        VALUES ($1, $2, $3, $4, $5)
    """
    valores = [
        (e.entregador_id, e.nome, e.tipo_veiculo, e.endereco_latitude, e.endereco_longitude)
        for e in entregadores
    ]
    try:
        await conn.executemany(query, valores)
        return {"mensagem": f"{len(entregadores)} Entregadores cadastrados com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cadastro/entregadores", response_model=List[Entregador])
async def listar_entregadores(conn = Depends(get_db_connection)):
    """Lista todos os entregadores cadastrados"""

    query = f"SELECT * FROM {SCHEMA}.ENTREGADOR"
    try:
        registros = await conn.fetch(query)
        return [dict(registro) for registro in registros]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cadastro/entregadores/{entregador_id}", response_model=Entregador)
async def obter_entregador(entregador_id: str, conn = Depends(get_db_connection)):
    """Busca um entregador específico pelo ID"""

    query = f"SELECT * FROM {SCHEMA}.ENTREGADOR WHERE ENTREGADOR_ID = $1"
    try:
        registro = await conn.fetchrow(query, entregador_id)
        if not registro:
            raise HTTPException(status_code=404, detail="Entregador não encontrado")
        return dict(registro)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================================================
# PRODUTOS
# ==================================================

@app.post("/cadastro/produtos", status_code=201)
async def cadastrar_produto(produto: Produto, conn = Depends(get_db_connection)):
    """Cadastra um novo produto no banco de dados"""

    query = f"""
        INSERT INTO {SCHEMA}.PRODUTOS
        (PROD_ID, NOME, REST_ID) 
        VALUES ($1, $2, $3)
    """
    valores = (
        produto.prod_id, produto.nome, produto.rest_id
    )
    try:
        await conn.execute(query, *valores)
        return {"mensagem": "Produto cadastrado com sucesso!", "id": produto.prod_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cadastro/produtos/batch", status_code=201)
async def cadastrar_produtos_batch(produtos: List[Produto], conn = Depends(get_db_connection)):
    """Cadastra múltiplos produtos de uma vez"""

    query = f"""
        INSERT INTO {SCHEMA}.PRODUTOS
        (PROD_ID, NOME, REST_ID) 
        VALUES ($1, $2, $3)
    """
    valores = [
        (p.prod_id, p.nome, p.rest_id)
        for p in produtos
    ]
    try:
        await conn.executemany(query, valores)
        return {"mensagem": f"{len(produtos)} Produtos cadastrados com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================================================
# PEDIDOS
# ==================================================

@app.post("/cadastro/pedidos", status_code=201)
async def cadastrar_pedido(pedido: Pedido, conn = Depends(get_db_connection)):
    """Cadastra um novo pedido no banco de dados"""

    query = f"""
        INSERT INTO {SCHEMA}.PEDIDOS 
        (PEDIDO_ID, USER_ID, REST_ID, ENTREGADOR_ID, CONFIRMED_TIME, PREPARING_TIME, READY_FOR_PICKUP_TIME, PICKED_UP_TIME, IN_TRANSIT_TIME, DELIVERED_TIME) 
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    """
    valores = (
        pedido.pedido_id, pedido.user_id, pedido.rest_id, pedido.entregador_id,
        pedido.confirmed_time, pedido.preparing_time, pedido.ready_for_pickup_time,
        pedido.picked_up_time, pedido.in_transit_time, pedido.delivered_time
    )
    try:
        await conn.execute(query, *valores)
        return {"mensagem": "Pedido cadastrado com sucesso!", "id": pedido.pedido_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

