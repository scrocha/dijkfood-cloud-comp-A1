import os
import math
import boto3
import osmnx as ox
import networkx as nx
import numpy as np
import asyncio
from scipy.spatial import cKDTree

from fastapi import FastAPI, HTTPException, Depends
from typing import Any, Dict, Tuple, List, Optional

from route_service.models import (
    EntregadorRequest, Ponto, RotaRequest, 
    EntregadorResponse, RotaEntregaResponse
)

GRAPH_FILE_NAME = "grafo_sp.graphml"
AWS_BUCKET_NAME = "grafo-dijkfood-sp-1"
AWS_REGION = "us-east-1"
EDGE_WEIGHT = "length"

def load_graph() -> nx.MultiDiGraph:
    if not os.path.exists(GRAPH_FILE_NAME):
        print("Baixando grafo do S3...")
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.download_file(AWS_BUCKET_NAME, GRAPH_FILE_NAME, GRAPH_FILE_NAME)
            
    print("Carregando grafo na memória")
    return ox.load_graphml(GRAPH_FILE_NAME)

def build_spatial_index(G: nx.MultiDiGraph) -> Tuple[List[int], cKDTree]:
    print("Construindo Índice Espacial")
    nodes = list(G.nodes(data=True))
    node_ids = [n[0] for n in nodes]
    coords = np.array([[n[1]['y'], n[1]['x']] for n in nodes], dtype=np.float32)
    tree = cKDTree(coords)
    return node_ids, tree

GLOBAL_GRAPH = load_graph()
GLOBAL_NODE_IDS, GLOBAL_TREE = build_spatial_index(GLOBAL_GRAPH)
print("Dados carregados. API pronta para receber requisições.")

app = FastAPI(
    title="DijkFood API - Serviço de Rotas (Extreme Performance)"
)

def get_graph() -> nx.MultiDiGraph: return GLOBAL_GRAPH
def get_tree() -> cKDTree: return GLOBAL_TREE
def get_node_ids() -> List[int]: return GLOBAL_NODE_IDS

def obter_no_mais_proximo(lat: float, lon: float, tree: cKDTree, node_ids: List[int]) -> int:
    """Busca em O(log N) no C via SciPy."""
    _, idx = tree.query([lat, lon])
    return node_ids[idx]

def projetar_ponto(ponto: Ponto, G: nx.MultiDiGraph, tree: cKDTree, node_ids: List[int]) -> Tuple[int, Ponto]:
    node_id = obter_no_mais_proximo(ponto.lat, ponto.lon, tree, node_ids)
    ponto_projetado = Ponto(lat=G.nodes[node_id]["y"], lon=G.nodes[node_id]["x"])
    return node_id, ponto_projetado

def encontrar_top_n_entregadores(restaurante: Ponto, entregadores: list[Ponto], n: int = 3) -> list[int]:
    """Usa matemática vetorizada no NumPy com correção de distorção esférica."""
    coords_entregadores = np.array([(e.lat, e.lon) for e in entregadores], dtype=np.float32)
    lat_rest, lon_rest = restaurante.lat, restaurante.lon
    
    cos_lat = math.cos(math.radians(lat_rest))
    
    diff = coords_entregadores - np.array([lat_rest, lon_rest], dtype=np.float32)
    diff[:, 1] *= cos_lat 
    
    distancias_sq = np.sum(diff ** 2, axis=1)
    
    n = min(n, len(entregadores))
    top_n_idx = np.argpartition(distancias_sq, n - 1)[:n]
    return top_n_idx.tolist()

def criar_heuristica_otimizada(G: nx.MultiDiGraph, no_destino: int):
    """Factory de heurística: pré-calcula constantes do destino para o A*."""
    lat2 = math.radians(G.nodes[no_destino]["y"])
    lon2 = math.radians(G.nodes[no_destino]["x"])
    cos_lat2 = math.cos(lat2)
    R = 6371000.0

    def calcular_heuristica(u, v):
        lat1 = math.radians(G.nodes[u]["y"])
        lon1 = math.radians(G.nodes[u]["x"])
        x = (lon2 - lon1) * cos_lat2
        y = (lat2 - lat1)
        return R * math.hypot(x, y) 
    
    return calcular_heuristica

def extrair_comprimento_do_caminho(G: nx.MultiDiGraph, caminho: list) -> float:
    comprimento = 0.0
    for u, v in zip(caminho[:-1], caminho[1:]):
        menor_aresta = min(G[u][v].values(), key=lambda aresta: aresta.get(EDGE_WEIGHT, float('inf')))
        comprimento += menor_aresta.get(EDGE_WEIGHT, 0.0)
    return comprimento

# --- Lógica Assíncrona e Endpoints ---

async def calcular_rota_async(
    origem: Ponto, destino: Ponto, G: nx.MultiDiGraph, tree: cKDTree, node_ids: List[int]
) -> Optional[Dict[str, Any]]:
    
    tarefa_origem = asyncio.to_thread(projetar_ponto, origem, G, tree, node_ids)
    tarefa_destino = asyncio.to_thread(projetar_ponto, destino, G, tree, node_ids)
    
    (no_origem, ponto_origem_proj), (no_destino, ponto_destino_proj) = await asyncio.gather(
        tarefa_origem, tarefa_destino
    )

    if no_origem == no_destino:
        return {
            "distancia_metros": 0.0,
            "nos": 1,
            "coordenadas": [{"lat": ponto_origem_proj.lat, "lon": ponto_origem_proj.lon}],
            "origem_projetada": ponto_origem_proj,
            "destino_projetado": ponto_destino_proj
        }

    try:
        heuristica = criar_heuristica_otimizada(G, no_destino)
        caminho = await asyncio.to_thread(
            nx.astar_path, G, no_origem, no_destino, 
            heuristic=heuristica, weight=EDGE_WEIGHT
        )
        comprimento = await asyncio.to_thread(extrair_comprimento_do_caminho, G, caminho)
        
    except nx.NetworkXNoPath:
        return None 

    coords = [{"lat": G.nodes[n]["y"], "lon": G.nodes[n]["x"]} for n in caminho]

    return {
        "distancia_metros": round(comprimento, 2),
        "nos": len(caminho),
        "coordenadas": coords,
        "origem_projetada": ponto_origem_proj,
        "destino_projetado": ponto_destino_proj
    }

@app.post("/entregador-mais-proximo", response_model=EntregadorResponse)
async def entregador_mais_proximo(
    req: EntregadorRequest, 
    G: nx.MultiDiGraph = Depends(get_graph),
    tree: cKDTree = Depends(get_tree),
    node_ids: List[int] = Depends(get_node_ids)
):
    top_n_indices = await asyncio.to_thread(
        encontrar_top_n_entregadores, req.restaurante, req.entregadores, n=5
    )
    
    tarefas_rotas = [
        calcular_rota_async(req.entregadores[idx], req.restaurante, G, tree, node_ids)
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
        raise HTTPException(status_code=404, detail="Não foi possível rotear nenhum dos entregadores próximos até o restaurante.")

    return {
        "entregador_idx": melhor_idx,
        "entregador_original": req.entregadores[melhor_idx],
        "rota_ao_restaurante": melhor_rota
    }

@app.post("/rota-entrega", response_model=RotaEntregaResponse)
async def rota_entrega(
    req: RotaRequest, 
    G: nx.MultiDiGraph = Depends(get_graph),
    tree: cKDTree = Depends(get_tree),
    node_ids: List[int] = Depends(get_node_ids)
):
    rota = await calcular_rota_async(req.origem, req.destino, G, tree, node_ids)
    
    if not rota:
         raise HTTPException(status_code=404, detail="Não existe rota viária possível entre os pontos informados.")
         
    return {
        "restaurante_solicitado": req.origem,
        "cliente_solicitado": req.destino,
        "dados_rota": rota
    }

@app.get("/health")
def health(G: nx.MultiDiGraph = Depends(get_graph)):
    return {
        "status": "ok", 
        "nos_no_grafo": len(G.nodes),
        "engine": "pydantic_v2 + global_state"
    }