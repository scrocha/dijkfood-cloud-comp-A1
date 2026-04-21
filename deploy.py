"""
Orquestrador de Deploy DijkFood.
Executa o deploy da infraestrutura principal e, em seguida, o deploy dos simuladores.

Uso:
    python deploy.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DEPLOY_INFRA = ROOT_DIR / "deploy_infra.py"
DEPLOY_SIMULADORES = ROOT_DIR / "deploy_simuladores.py"
DEPLOY_OUTPUT = ROOT_DIR / "deploy_output.json"
ALB_ENDPOINTS = ROOT_DIR / "alb_endpoints.json"
SIMULATOR_OUTPUT = ROOT_DIR / "simulador_ecs" / "simulador_output.json"


def run_script(script_path, args=None):
    if args is None:
        args = []

    cmd = [sys.executable, str(script_path)] + args
    print(f"\n>>> Executando: {' '.join(cmd)}")

    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(
            f"\n!!! Erro ao executar {script_path.name} (Exit code: {result.returncode})"
        )
        return False
    return True


def verify_artifacts():
    missing = []
    for artifact in (DEPLOY_OUTPUT, ALB_ENDPOINTS, SIMULATOR_OUTPUT):
        if not artifact.exists():
            missing.append(artifact)

    if missing:
        print("\nArtefatos esperados não encontrados após o deploy:")
        for artifact in missing:
            print(f"    - {artifact.name}")
        return False

    try:
        deploy_data = json.loads(DEPLOY_OUTPUT.read_text())
        alb_data = json.loads(ALB_ENDPOINTS.read_text())
        sim_data = json.loads(SIMULATOR_OUTPUT.read_text())
    except Exception as e:
        print(f"\n!!! Erro ao validar arquivos de saída do deploy: {e}")
        return False

    required_deploy_keys = {"API_URL", "ALB_DNS", "SG_ID", "VPC_ID", "SUBNET_IDS"}
    required_alb_keys = {"cadastro", "rotas", "pedidos"}
    required_sim_keys = {"CLUSTER_NAME", "SG_ID", "VPC_ID", "SUBNET_IDS", "SIM_ALB_DNS", "SIM_ALB_URL", "SIMULATORS"}

    if not required_deploy_keys.issubset(deploy_data):
        print("\ndeploy_output.json não contém todas as chaves esperadas.")
        return False
    if not required_alb_keys.issubset(alb_data):
        print("\nalb_endpoints.json não contém todas as rotas esperadas.")
        return False
    if not required_sim_keys.issubset(sim_data):
        print("\nsimulador_output.json não contém todas as chaves esperadas.")
        return False

    print("\n>>> Artefatos de deploy validados com sucesso")
    print(f"    API principal: {deploy_data['API_URL']}")
    print(f"    ALB simuladores: {sim_data['SIM_ALB_URL']}")
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
    print("Infraestrutura base pronta. Iniciando simuladores")
    print("-" * 40 + "\n")

    # 2. Deploy dos Simuladores (Cluster ECS dedicado, ALB interno)
    if not run_script(DEPLOY_SIMULADORES):
        print("\nFalha no deploy dos simuladores.")
        sys.exit(1)

    if not verify_artifacts():
        print("\nFalha na validação dos artefatos gerados pelo deploy.")
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
