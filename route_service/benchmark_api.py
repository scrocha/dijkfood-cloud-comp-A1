import time
import random
import osmnx as ox
import networkx as nx
from fastapi.testclient import TestClient

from route_service.api import app as app_new

# Inicializa o cliente de teste
client_new = TestClient(app_new)

GRAPH_FILE_NAME = "grafo_sp.graphml"

def load_graph() -> nx.MultiDiGraph:
    return ox.load_graphml(GRAPH_FILE_NAME)

# Extrai os dados dos nós do grafo para gerar pontos válidos e evitar rotas impossíveis
nodes_data = list(load_graph().nodes(data=True))

def gerar_ponto_valido():
    """Gera coordenadas de um nó que sabidamente existe no grafo."""
    _, data = random.choice(nodes_data)
    return {
        "lat": data['y'],
        "lon": data['x']
    }

def executar_benchmark_entregadores(qtd_entregadores: int, rodadas: int = 3):
    """Testa a capacidade do Numpy de processar matrizes gigantes e do A* em seguida."""
    print(f"\n--- [Entregador Mais Próximo] Testando com {qtd_entregadores} entregadores ({rodadas} rodadas) ---")
    
    tempo_total = 0.0
    sucessos = 0
    
    for _ in range(rodadas):
        payload = {
            "restaurante": gerar_ponto_valido(),
            "entregadores": [gerar_ponto_valido() for _ in range(qtd_entregadores)]
        }
        
        start = time.time()
        res = client_new.post("/entregador-mais-proximo", json=payload)
        tempo_total += (time.time() - start)

        if res.status_code == 200:
            sucessos += 1

    media = tempo_total / rodadas
    print(f"Tempo Médio da Requisição: {media:.4f} segundos")
    print(f"Requisições bem sucedidas (200 OK): {sucessos}/{rodadas}")


def executar_benchmark_rotas(rodadas: int = 100):
    """Estressa o endpoint de rotas para avaliar a performance bruta do algoritmo A*."""
    print(f"\n--- [Cálculo de Rotas A*] Estresse com {rodadas} rotas consecutivas ---")
    
    tempo_total = 0.0
    sucessos = 0
    erros = 0
    
    start_global = time.time()
    
    for _ in range(rodadas):
        # Gera pares de origem e destino aleatórios para forçar cálculos complexos
        payload = {
            "origem": gerar_ponto_valido(),
            "destino": gerar_ponto_valido()
        }
        
        start = time.time()
        res = client_new.post("/rota-entrega", json=payload)
        tempo_total += (time.time() - start)

        if res.status_code == 200:
            sucessos += 1
        else:
            erros += 1

    tempo_global = time.time() - start_global
    media = tempo_total / rodadas
    
    print(f"Tempo Total do Teste: {tempo_global:.2f} segundos")
    print(f"Tempo Médio por Rota: {media:.4f} segundos")
    print(f"Sucessos (200 OK): {sucessos}/{rodadas}")
    if erros > 0:
        print(f"⚠️ Atenção: {erros} rotas falharam (provavelmente sem caminho possível no grafo).")

if __name__ == "__main__":
    # 1. Configuração dos cenários
    cenarios_entregadores = [5, 10000, 100000, 500000, 1000000]  # Testa desde poucos entregadores até 1 milhão
    cenario_estresse_rotas = 50  # Quantidade de requisições sequenciais para estressar o A*
    
    print("Iniciando Benchmarks de Performance (DijkFood)...")
    
    # # 2. Roda o benchmark focado no endpoint do entregador
    for qtd in cenarios_entregadores:
        executar_benchmark_entregadores(qtd_entregadores=qtd, rodadas=3)
        
    # 3. Roda o benchmark de estresse focado puramente no roteamento geográfico
    executar_benchmark_rotas(rodadas=cenario_estresse_rotas)