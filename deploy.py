"""
Orquestrador de Deploy DijkFood.
Executa o deploy da infraestrutura principal e, em seguida, o deploy dos simuladores.

Uso:
    python deploy.py
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DEPLOY_INFRA = ROOT_DIR / "deploy_infra.py"
DEPLOY_SIMULADORES = ROOT_DIR / "deploy_simuladores.py"

def run_script(script_path, args=None):
    if args is None:
        args = []
    
    cmd = [sys.executable, str(script_path)] + args
    print(f"\n>>> Executando: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"\n!!! Erro ao executar {script_path.name} (Exit code: {result.returncode})")
        return False
    return True

def main():
    start_time = time.time()
    
    print("=" * 60)
    print("INICIANDO DEPLOY COMPLETO DIJKFOOD")
    print("=" * 60)

    # 1. Deploy da Infraestrutura Base (RDS, Dynamo, APIs principais)
    if not run_script(DEPLOY_INFRA):
        print("\nFalha no deploy da infraestrutura. Abortando simuladores.")
        sys.exit(1)

    print("\n" + "-" * 40)
    print("Infraestrutura base pronta. Iniciando simuladores...")
    print("-" * 40 + "\n")

    # 2. Deploy dos Simuladores (Cluster ECS dedicado, ALB interno)
    if not run_script(DEPLOY_SIMULADORES):
        print("\nFalha no deploy dos simuladores.")
        sys.exit(1)

    end_time = time.time()
    duration = (end_time - start_time) / 60

    print("\n" + "=" * 60)
    print(f"DEPLOY COMPLETO FINALIZADO EM {duration:.2f} MINUTOS")
    print("=" * 60)
    print("Tudo pronto! Você pode acessar o dashboard com:")
    print("  uv run streamlit run simulador_ecs/dashboard_carga.py")
    print("=" * 60)

if __name__ == "__main__":
    main()
