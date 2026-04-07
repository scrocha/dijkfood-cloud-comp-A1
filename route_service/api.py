import math
import gc
import pickle
import numpy as np
import asyncio
from functools import lru_cache
from scipy.spatial import cKDTree
from pathlib import Path

import rustworkx as rx 

from fastapi import FastAPI, HTTPException
from typing import Any, Dict, Tuple, List, Optional

from route_service.models import (
    EntregadorRequest, Ponto, RotaRequest, 
    EntregadorResponse, RotaEntregaResponse,
    RotaResponseData, Percurso
)

_DIR = Path(__file__).parent

PKL_FILE_NAME = _DIR / "grafo_sp.pkl"

# Variáveis Globais
GLOBAL_GRAPH: rx.PyGraph = None
GLOBAL_NODE_COORDS: np.ndarray = None
GLOBAL_TREE: cKDTree = None

def startup_optimization():
    global GLOBAL_GRAPH, GLOBAL_NODE_COORDS, GLOBAL_TREE

    if not PKL_FILE_NAME.exists():
        from route_service.download_graph import preparar_dados
        preparar_dados()

    with open(PKL_FILE_NAME, "rb") as f:
        dados = pickle.load(f)

    GLOBAL_NODE_COORDS = dados["coords"]
    arestas = dados["arestas"]

    GLOBAL_GRAPH = rx.PyGraph()
    
    GLOBAL_GRAPH.add_nodes_from(range(len(GLOBAL_NODE_COORDS)))
    
    arestas_rx = [(u, v, float(w)) for u, v, w in arestas]
    GLOBAL_GRAPH.add_edges_from(arestas_rx)

    GLOBAL_TREE = cKDTree(GLOBAL_NODE_COORDS)

    del dados
    del arestas
    del arestas_rx
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
    return int(idx)

def projetar_ponto(ponto: Ponto) -> Tuple[int, Ponto]:
    lat_rnd, lon_rnd = round(ponto.lat, 5), round(ponto.lon, 5)
    node_id = obter_no_mais_proximo_cache(lat_rnd, lon_rnd)
    
    lat_proj = GLOBAL_NODE_COORDS[node_id, 0]
    lon_proj = GLOBAL_NODE_COORDS[node_id, 1]
    return node_id, Ponto(lat=float(lat_proj), lon=float(lon_proj))

def encontrar_top_n_entregadores(restaurante: Ponto, entregadores: List[Ponto], n: int = 5) -> List[int]:
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
    lat2_deg = GLOBAL_NODE_COORDS[no_destino, 0]
    lon2_deg = GLOBAL_NODE_COORDS[no_destino, 1]
    
    lat2 = math.radians(lat2_deg)
    lon2 = math.radians(lon2_deg)
    cos_lat2 = math.cos(lat2)
    R = 6371000.0

    def calcular_heuristica(u_idx):
        lat1_deg = GLOBAL_NODE_COORDS[u_idx, 0]
        lon1_deg = GLOBAL_NODE_COORDS[u_idx, 1]
        
        lat1 = math.radians(lat1_deg)
        lon1 = math.radians(lon1_deg)
        
        x = (lon2 - lon1) * cos_lat2
        y = (lat2 - lat1)
        return R * math.hypot(x, y) 
    
    return calcular_heuristica

def calcular_distancia_haversine(ponto1: Ponto, ponto2: Ponto) -> float:
    lat1 = math.radians(ponto1.lat)
    lat2 = math.radians(ponto2.lat)
    lon1 = math.radians(ponto1.lon)
    lon2 = math.radians(ponto2.lon)
    cos_lat = math.cos((lat1 + lat2) / 2.0)
    x = (lon2 - lon1) * cos_lat
    y = lat2 - lat1
    return 6371000.0 * math.hypot(x, y)

def extrair_segmentos_do_caminho(G: rx.PyGraph, caminho_rx: List[int], node_coords: np.ndarray) -> Tuple[List[Percurso], float]:
    segmentos: List[Percurso] = []
    comprimento_total = 0.0
    
    for u_idx, v_idx in zip(caminho_rx[:-1], caminho_rx[1:]):
        comprimento = G.get_edge_data(u_idx, v_idx)
        comprimento_total += comprimento
        
        segmentos.append(Percurso(
            ponto_origem=Ponto(lat=float(node_coords[u_idx, 0]), lon=float(node_coords[u_idx, 1])),
            ponto_fim=Ponto(lat=float(node_coords[v_idx, 0]), lon=float(node_coords[v_idx, 1])),
            comprimento=comprimento
        ))
    return segmentos, comprimento_total

def resolver_astar(no_origem: int, no_destino: int) -> Optional[List[int]]:
    heuristica = criar_heuristica_otimizada(no_destino)
    
    def goal_fn(n):
        return n == no_destino
    
    def edge_cost_fn(e):
        return float(e)
        
    try:
        caminho_rx = rx.astar_shortest_path(
            GLOBAL_GRAPH,
            no_origem,
            goal_fn,
            edge_cost_fn,
            heuristica
        )
        return caminho_rx
    except Exception: 
        return None


async def calcular_rota_async(origem: Ponto, destino: Ponto) -> Optional[RotaResponseData]:
    no_origem, ponto_origem_proj = await asyncio.to_thread(projetar_ponto, origem)
    no_destino, ponto_destino_proj = await asyncio.to_thread(projetar_ponto, destino)

    percurso_inicial = Percurso(
        ponto_origem=origem,
        ponto_fim=ponto_origem_proj,
        comprimento=round(calcular_distancia_haversine(origem, ponto_origem_proj), 2)
    )
    percurso_final = Percurso(
        ponto_origem=ponto_destino_proj,
        ponto_fim=destino,
        comprimento=round(calcular_distancia_haversine(ponto_destino_proj, destino), 2)
    )

    if no_origem == no_destino:
        return RotaResponseData(
            distancia_metros=0.0, nos=1,
            percursos=[percurso_inicial, percurso_final],
            origem_projetada=ponto_origem_proj, destino_projetado=ponto_destino_proj
        )

    caminho_rx = await asyncio.to_thread(resolver_astar, no_origem, no_destino)
    
    if not caminho_rx:
        return None 

    segmentos, comprimento = await asyncio.to_thread(
        extrair_segmentos_do_caminho, GLOBAL_GRAPH, caminho_rx, GLOBAL_NODE_COORDS
    )

    return RotaResponseData(
        distancia_metros=round(comprimento, 2),
        nos=len(caminho_rx),
        percursos=[percurso_inicial] + segmentos + [percurso_final],
        origem_projetada=ponto_origem_proj,
        destino_projetado=ponto_destino_proj
    )

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
    
    melhor_rota: Optional[RotaResponseData] = None
    melhor_idx: int = -1
    menor_distancia: float = float('inf')
    
    for idx, rota in zip(top_n_indices, resultados_rotas):
        if rota and rota.distancia_metros < menor_distancia:
            menor_distancia = rota.distancia_metros
            melhor_rota = rota
            melhor_idx = idx

    if not melhor_rota:
        raise HTTPException(status_code=404, detail="Não foi possível rotear nenhum dos entregadores próximos.")

    return EntregadorResponse(
        entregador_idx=melhor_idx,
        entregador_original=req.entregadores[melhor_idx],
        rota_ao_restaurante=melhor_rota
    )

@app.post("/rotas/rota-entrega", response_model=RotaEntregaResponse)
async def rota_entrega(req: RotaRequest):
    rota = await calcular_rota_async(req.origem, req.destino)
    
    if not rota:
         raise HTTPException(status_code=404, detail="Não existe rota viária possível.")
         
    return RotaEntregaResponse(
        restaurante_solicitado=req.origem,
        cliente_solicitado=req.destino,
        dados_rota=rota
    )

@app.get("/rotas/health")
def health():
    return {
        "status": "ok",
        "nos_no_grafo": GLOBAL_GRAPH.num_nodes(),
        "arestas_no_grafo": GLOBAL_GRAPH.num_edges(),
    }