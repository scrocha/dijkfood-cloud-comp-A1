import time
import random
import requests
import sys

# URL da API Docker (Mapeada localmente)
BASE_URL = "http://localhost:8000"

def gerar_ponto_valido():
    # Coordenadas aproximadas de São Paulo para evitar erros no início
    # O grafo será carregado no container, e lá ele fará o cKDTree query
    return {
        "lat": -23.55 + (random.random() - 0.5) * 0.1,
        "lon": -46.63 + (random.random() - 0.5) * 0.1
    }

def executar_benchmark_entregadores(qtd_entregadores: int, rodadas: int = 3):
    print(f"\n--- [Docker-Load] {qtd_entregadores} entregadores ({rodadas} rodadas) ---")
    
    for r in range(rodadas):
        payload = {
            "restaurante": gerar_ponto_valido(),
            "entregadores": [gerar_ponto_valido() for _ in range(qtd_entregadores)]
        }
        
        start = time.time()
        try:
            res = requests.post(f"{BASE_URL}/entregador-mais-proximo", json=payload, timeout=60)
            elapsed = time.time() - start
            print(f"Rodada {r+1}: {res.status_code} em {elapsed:.4f}s")
        except Exception as e:
            print(f"Erro na rodada {r+1}: {e}")

def executar_benchmark_rotas(rodadas: int = 50):
    print(f"\n--- [Docker-Load] {rodadas} rotas consecutivas ---")
    
    for r in range(rodadas):
        payload = {
            "origem": gerar_ponto_valido(),
            "destino": gerar_ponto_valido()
        }
        
        start = time.time()
        try:
            res = requests.post(f"{BASE_URL}/rota-entrega", json=payload, timeout=30)
            elapsed = time.time() - start
            print(f"Rota {r+1}: {res.status_code} em {elapsed:.4f}s")
        except Exception as e:
            print(f"Erro na rota {r+1}: {e}")

if __name__ == "__main__":
    print("Aguardando o container carregar (health-check)...")
    max_retries = 30
    for _ in range(max_retries):
        try:
            res = requests.get(f"{BASE_URL}/health")
            if res.status_code == 200:
                print("API Online!")
                break
        except:
            time.sleep(2)
    else:
        print("API não ficou pronta a tempo.")
        sys.exit(1)

    # Inicia os testes
    executar_benchmark_entregadores(100000, rodadas=3)
    executar_benchmark_entregadores(1000000, rodadas=3)
    executar_benchmark_rotas(50)
