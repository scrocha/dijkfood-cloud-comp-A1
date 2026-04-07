import time
import random
import requests
import sys
import numpy as np
from statistics import mean, median

# URL da API Docker (Mapeada localmente)
BASE_URL = "http://localhost:8001"

def gerar_ponto_valido():
    # Coordenadas aproximadas de São Paulo para evitar erros no início
    # O grafo será carregado no container, e lá ele fará o cKDTree query
    return {
        "lat": -23.55 + (random.random() - 0.5) * 0.1,
        "lon": -46.63 + (random.random() - 0.5) * 0.1
    }

def executar_benchmark_entregadores(qtd_entregadores: int, rodadas: int = 3):
    print(f"\n--- [Docker-Load] Testing {qtd_entregadores:,} entregadores ({rodadas} rodadas) ---")
    tempos = []
    
    for r in range(rodadas):
        payload = {
            "restaurante": gerar_ponto_valido(),
            "entregadores": [gerar_ponto_valido() for _ in range(qtd_entregadores)]
        }
        
        start = time.time()
        try:
            res = requests.post(f"{BASE_URL}/rotas/entregador-mais-proximo", json=payload, timeout=60)
            elapsed = time.time() - start
            if res.status_code == 200:
                tempos.append(elapsed)
                print(f"  Rodada {r+1}: {res.status_code} em {elapsed:.4f}s")
            else:
                print(f"  Rodada {r+1}: ERRO {res.status_code} - {res.text}")
        except Exception as e:
            print(f"  Erro na rodada {r+1}: {e}")
    
    if tempos:
        print(f"  [RESULTADOS] Média: {mean(tempos):.4f}s | Mediana: {median(tempos):.4f}s | Vazão: {qtd_entregadores/mean(tempos):.2f} ent/s")

def executar_benchmark_rotas(rodadas: int = 50):
    print(f"\n--- [Docker-Load] Testing {rodadas} rotas consecutivas ---")
    tempos = []
    erros = 0
    
    for r in range(rodadas):
        payload = {
            "origem": gerar_ponto_valido(),
            "destino": gerar_ponto_valido()
        }
        
        start = time.time()
        try:
            res = requests.post(f"{BASE_URL}/rotas/rota-entrega", json=payload, timeout=30)
            elapsed = time.time() - start
            if res.status_code == 200:
                tempos.append(elapsed)
                if (r + 1) % (rodadas // 5) == 0 or r == 0:
                    print(f"  Rota {r+1}/{rodadas}: {res.status_code} em {elapsed:.4f}s")
            else:
                erros += 1
        except Exception as e:
            erros += 1

    if tempos:
        print(f"  [RESULTADOS] Min: {min(tempos):.4f}s | Max: {max(tempos):.4f}s | Média: {mean(tempos):.4f}s")
        print(f"  Percentis: P95: {np.percentile(tempos, 95):.4f}s | P99: {np.percentile(tempos, 99):.4f}s")
        print(f"  Total de erros: {erros}")

if __name__ == "__main__":
    print("Aguardando o container carregar (health-check)...")
    max_retries = 30
    for _ in range(max_retries):
        try:
            res = requests.get(f"{BASE_URL}/rotas/health")
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
    executar_benchmark_rotas(1000)
