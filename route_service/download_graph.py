from pathlib import Path
import pickle
import osmnx as ox
import numpy as np

_DIR = Path(__file__).parent
GRAPH_FILE_NAME = _DIR / "grafo_sp.graphml"
PKL_FILE_NAME = _DIR / "grafo_sp.pkl"
EDGE_WEIGHT = "length"
PLACE_NAME = "Sao Paulo, SP, Brazil"

def preparar_dados():
    if PKL_FILE_NAME.exists():
        return
    
    if GRAPH_FILE_NAME.exists():
        multi_g = ox.load_graphml(GRAPH_FILE_NAME)
    else:
        multi_g = ox.graph_from_place(PLACE_NAME, network_type="drive")
        ox.save_graphml(multi_g, GRAPH_FILE_NAME)
    
    multi_g = multi_g.to_undirected()
    
    osm_nodes = list(multi_g.nodes(data=True))
    N = len(osm_nodes)
    osm_id_to_idx = {}
    coords = np.zeros((N, 2), dtype=np.float32)
    
    for idx, (node_id, data) in enumerate(osm_nodes):
        osm_id_to_idx[node_id] = idx
        coords[idx, 0] = data['y']
        coords[idx, 1] = data['x']

    arestas_dict = {}
    for u, v, data in multi_g.edges(data=True):
        w = data.get(EDGE_WEIGHT, float('inf'))
        w = min(w) if isinstance(w, list) else float(w)
        u_idx, v_idx = osm_id_to_idx[u], osm_id_to_idx[v]
        
        if (u_idx, v_idx) not in arestas_dict or w < arestas_dict[(u_idx, v_idx)]:
            arestas_dict[(u_idx, v_idx)] = w
            
    arestas = [(u, v, w) for (u, v), w in arestas_dict.items()]
    with open(PKL_FILE_NAME, "wb") as f:
        pickle.dump({"coords": coords, "arestas": arestas}, f)

if __name__ == "__main__":
    preparar_dados()