from pathlib import Path
import pickle
import osmnx as ox

_DIR = Path(__file__).parent

GRAPH_FILE_NAME = _DIR / "grafo_sp.graphml"
PKL_FILE_NAME = _DIR / "grafo_sp.pkl"
EDGE_WEIGHT = "length"

PLACE_NAME = "Sao Paulo, SP, Brazil"

def preparar_dados():
    # 1. Verifica se o .pkl final já existe
    if PKL_FILE_NAME.exists():
        return

    # 2. Se não existir, verifica se o .graphml já foi baixado
    if GRAPH_FILE_NAME.exists():
        multi_g = ox.load_graphml(GRAPH_FILE_NAME)
    else:
        multi_g = ox.graph_from_place(PLACE_NAME, network_type="drive")
        ox.save_graphml(multi_g, GRAPH_FILE_NAME)
    
    nos = {}
    for node_id, data in multi_g.nodes(data=True):
        nos[node_id] = (data['y'], data['x'])

    arestas_dict = {}
    for u, v, data in multi_g.edges(data=True):
        w = data.get(EDGE_WEIGHT, float('inf'))
        if isinstance(w, list): 
            w = min(w)
        w = float(w)
        
        if (u, v) not in arestas_dict or w < arestas_dict[(u, v)]:
            arestas_dict[(u, v)] = w
    
    arestas = [(u, v, w) for (u, v), w in arestas_dict.items()]

    with open(PKL_FILE_NAME, "wb") as f:
        pickle.dump({"nos": nos, "arestas": arestas}, f)

if __name__ == "__main__":
    preparar_dados()