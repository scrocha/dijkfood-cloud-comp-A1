import os
import math
import gc
import pickle
import networkx as nx
import numpy as np
import asyncio
from functools import lru_cache
from scipy.spatial import cKDTree
from pathlib import Path

from fastapi import FastAPI, HTTPException
from typing import Any, Dict, Tuple, List, Optional

from route_service.models import (
    EntregadorRequest, Ponto, RotaRequest, 
    EntregadorResponse, RotaEntregaResponse
)

_DIR = Path(__file__).parent

PKL_FILE_NAME = _DIR / "grafo_sp.pkl"
EDGE_WEIGHT = "length"

GLOBAL_GRAPH: nx.DiGraph = None
GLOBAL_NODE_COORDS: Dict[int, Tuple[float, float]] = {}
GLOBAL_TREE: cKDTree = None
GLOBAL_NODE_IDS: np.ndarray = None

def startup_optimization():
    global GLOBAL_GRAPH, GLOBAL_NODE_COORDS, GLOBAL_TREE, GLOBAL_NODE_IDS

    if not PKL_FILE_NAME.exists():
        from route_service.download_graph import preparar_dados
        preparar_dados()

    print(f"Carregando dados")
    with open(PKL_FILE_NAME, "rb") as f:
        dados = pickle.load(f)

    GLOBAL_GRAPH = nx.DiGraph()
    GLOBAL_NODE_COORDS = dados["nos"]
    
    coords_for_tree = []
    node_ids_list = []
    
    # 1. Popula os Nós e prepara dados para a KD-Tree
    for node_id, (lat, lon) in GLOBAL_NODE_COORDS.items():
        GLOBAL_GRAPH.add_node(node_id)
        coords_for_tree.append([lat, lon])
        node_ids_list.append(node_id)

    # 2. Popula as Arestas
    for u, v, w in dados["arestas"]:
        GLOBAL_GRAPH.add_edge(u, v, **{EDGE_WEIGHT: w})

    print("Grafo montado em memória com sucesso.")

    GLOBAL_TREE = cKDTree(np.array(coords_for_tree, dtype=np.float32))
    GLOBAL_NODE_IDS = np.array(node_ids_list)

    print("Árvore KD-Tree construída para busca de nós mais próximos")

    del dados
    del coords_for_tree
    del node_ids_list
    gc.collect()
    
    print("Api pronta para receber requisições")

startup_optimization()

app = FastAPI(
    title="DijkFood API - Serviço de Rotas",
    docs_url="/rotas/docs",
    redoc_url="/rotas/redoc",
    openapi_url="/rotas/openapi.json"
)

# --- Funções de Roteamento e Busca ---

@lru_cache(maxsize=5000)
def obter_no_mais_proximo_cache(lat_rnd: float, lon_rnd: float) -> int:
    _, idx = GLOBAL_TREE.query([lat_rnd, lon_rnd])
    return GLOBAL_NODE_IDS[idx]

def projetar_ponto(ponto: Ponto) -> Tuple[int, Ponto]:
    lat_rnd, lon_rnd = round(ponto.lat, 4), round(ponto.lon, 4)
    node_id = obter_no_mais_proximo_cache(lat_rnd, lon_rnd)
    
    lat_proj, lon_proj = GLOBAL_NODE_COORDS[node_id]
    return node_id, Ponto(lat=lat_proj, lon=lon_proj)

def encontrar_top_n_entregadores(restaurante: Ponto, entregadores: list[Ponto], n: int = 5) -> list[int]:
    coords_entregadores = np.array([(e.lat, e.lon) for e in entregadores], dtype=np.float32)
    lat_rest, lon_rest = restaurante.lat, restaurante.lon
    
    cos_lat = math.cos(math.radians(lat_rest))
    diff = coords_entregadores - np.array([lat_rest, lon_rest], dtype=np.float32)
    diff[:, 1] *= cos_lat 
    
    distancias_sq = np.sum(diff ** 2, axis=1)
    
    n = min(n, len(entregadores))
    top_n_idx = np.argpartition(distancias_sq, n - 1)[:n]
    return top_n_idx.tolist()

def criar_heuristica_otimizada(no_destino: int):
    lat2_deg, lon2_deg = GLOBAL_NODE_COORDS[no_destino]
    lat2 = math.radians(lat2_deg)
    lon2 = math.radians(lon2_deg)
    cos_lat2 = math.cos(lat2)
    R = 6371000.0

    def calcular_heuristica(u, v):
        lat1_deg, lon1_deg = GLOBAL_NODE_COORDS[u]
        lat1 = math.radians(lat1_deg)
        lon1 = math.radians(lon1_deg)
        
        x = (lon2 - lon1) * cos_lat2
        y = (lat2 - lat1)
        return R * math.hypot(x, y) 
    
    return calcular_heuristica

def calcular_distancia_haversine(ponto1: Ponto, ponto2: Ponto) -> float:
    """Calcula a distância aproximada entre dois pontos usando projeção equirretangular."""
    lat1 = math.radians(ponto1.lat)
    lat2 = math.radians(ponto2.lat)
    lon1 = math.radians(ponto1.lon)
    lon2 = math.radians(ponto2.lon)
    cos_lat = math.cos((lat1 + lat2) / 2.0)
    x = (lon2 - lon1) * cos_lat
    y = lat2 - lat1
    return 6371000.0 * math.hypot(x, y)

def extrair_segmentos_do_caminho(G: nx.DiGraph, caminho: list, node_coords: dict) -> Tuple[List[Dict], float]:
    segmentos = []
    comprimento_total = 0.0
    for u, v in zip(caminho[:-1], caminho[1:]):
        comprimento = G[u][v].get(EDGE_WEIGHT, 0.0)
        comprimento_total += comprimento
        segmentos.append({
            "ponto_origem": {"lat": node_coords[u][0], "lon": node_coords[u][1]},
            "ponto_fim": {"lat": node_coords[v][0], "lon": node_coords[v][1]},
            "comprimento": comprimento
        })
    return segmentos, comprimento_total

# --- Lógica Assíncrona e Endpoints ---

async def calcular_rota_async(origem: Ponto, destino: Ponto) -> Optional[Dict[str, Any]]:
    no_origem, ponto_origem_proj = await asyncio.to_thread(projetar_ponto, origem)
    no_destino, ponto_destino_proj = await asyncio.to_thread(projetar_ponto, destino)

    # Segmento do ponto de origem original ao ponto de origem reprojetado
    percurso_inicial = {
        "ponto_origem": {"lat": origem.lat, "lon": origem.lon},
        "ponto_fim": {"lat": ponto_origem_proj.lat, "lon": ponto_origem_proj.lon},
        "comprimento": round(calcular_distancia_haversine(origem, ponto_origem_proj), 2)
    }
    # Segmento do ponto de destino reprojetado ao ponto de destino original
    percurso_final = {
        "ponto_origem": {"lat": ponto_destino_proj.lat, "lon": ponto_destino_proj.lon},
        "ponto_fim": {"lat": destino.lat, "lon": destino.lon},
        "comprimento": round(calcular_distancia_haversine(ponto_destino_proj, destino), 2)
    }

    if no_origem == no_destino:
        return {
            "distancia_metros": 0.0, "nos": 1,
            "percurso_inicial": percurso_inicial,
            "percursos": [],
            "percurso_final": percurso_final,
            "origem_projetada": ponto_origem_proj, "destino_projetado": ponto_destino_proj
        }

    heuristica = criar_heuristica_otimizada(no_destino)

    try:
        caminho = await asyncio.to_thread(
            nx.astar_path, GLOBAL_GRAPH, no_origem, no_destino, 
            heuristic=heuristica, weight=EDGE_WEIGHT
        )
        segmentos, comprimento = await asyncio.to_thread(
            extrair_segmentos_do_caminho, GLOBAL_GRAPH, caminho, GLOBAL_NODE_COORDS
        )
        
    except nx.NetworkXNoPath:
        return None 

    return {
        "distancia_metros": round(comprimento, 2),
        "nos": len(caminho),
        "percursos": [percurso_inicial] + segmentos + [percurso_final],
        "origem_projetada": ponto_origem_proj,
        "destino_projetado": ponto_destino_proj
    }

@app.post("/rotas/entregador-mais-proximo", response_model=EntregadorResponse)
async def entregador_mais_proximo(req: EntregadorRequest):
    top_n_indices = await asyncio.to_thread(
        encontrar_top_n_entregadores, req.restaurante, req.entregadores, n=5
    )
    
    tarefas_rotas = [
        calcular_rota_async(req.entregadores[idx], req.restaurante)
        for idx in top_n_indices
    ]
    resultados_rotas = await asyncio.gather(*tarefas_rotas)
    
    melhor_rota = None
    melhor_idx = -1
    menor_distancia = float('inf')
    
    for idx, rota in zip(top_n_indices, resultados_rotas):
        if rota and rota["distancia_metros"] < menor_distancia:
            menor_distancia = rota["distancia_metros"]
            melhor_rota = rota
            melhor_idx = idx

    if not melhor_rota:
        raise HTTPException(status_code=404, detail="Não foi possível rotear nenhum dos entregadores próximos.")

    return {
        "entregador_idx": melhor_idx,
        "entregador_original": req.entregadores[melhor_idx],
        "rota_ao_restaurante": melhor_rota
    }

@app.post("/rotas/rota-entrega", response_model=RotaEntregaResponse)
async def rota_entrega(req: RotaRequest):
    rota = await calcular_rota_async(req.origem, req.destino)
    
    if not rota:
         raise HTTPException(status_code=404, detail="Não existe rota viária possível.")
         
    return {
        "restaurante_solicitado": req.origem,
        "cliente_solicitado": req.destino,
        "dados_rota": rota
    }

@app.get("/rotas/health")
def health():
    return {
        "status": "ok", 
        "nos_no_grafo": GLOBAL_GRAPH.number_of_nodes(),
        "arestas_no_grafo": GLOBAL_GRAPH.number_of_edges(),
        "cache_kdtree_stats": obter_no_mais_proximo_cache.cache_info()._asdict()
    }
