import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3

# Configurações de Caminhos
ROOT_DIR = Path(__file__).resolve().parent
DEPLOY_OUTPUT_PATH = ROOT_DIR / "deploy_output.json"
SIM_CONFIG_PATH = ROOT_DIR / "simulador_ecs" / "config.json"
BENCHMARK_RESULTS_PATH = ROOT_DIR / "benchmark_results.json"

# Carregar Configurações do Simulador
with open(SIM_CONFIG_PATH, "r") as f:
    sim_config_data = json.load(f)

AWS_REGION = sim_config_data["AWS_REGION"]
CLUSTER_SIM = sim_config_data["CLUSTER_NAME"]
LOG_GROUP_SIM = sim_config_data["LOG_GROUP_NAME"]
SIM_CLIENTES_INFO = sim_config_data["SIMULATORS"]["sim_pedidos"]

# Clientes Boto3
ecs = boto3.client("ecs", region_name=AWS_REGION)
logs = boto3.client("logs", region_name=AWS_REGION)
dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)


def get_order_stats(table_name="DijkfoodOrders"):
    """Consulta o DynamoDB para pegar contagem de pedidos por status."""
    def count_status(status):
        try:
            resp = dynamodb.query(
                TableName=table_name,
                IndexName="StatusIndex",
                Select="COUNT",
                KeyConditionExpression="GSI2PK = :g",
                ExpressionAttributeValues={":g": {"S": f"STATUS#{status}"}}
            )
            return resp.get("Count", 0)
        except:
            return 0

    return {
        "CONFIRMED": count_status("CONFIRMED"),
        "PREPARING": count_status("PREPARING"),
        "READY": count_status("READY_FOR_PICKUP"),
        "PICKED": count_status("PICKED_UP"),
        "TRANSIT": count_status("IN_TRANSIT"),
        "DELIVERED": count_status("DELIVERED")
    }


def get_last_p95():
    """Pega a métrica P95 mais recente dos logs."""
    prefix = SIM_CLIENTES_INFO["LOG_STREAM_PREFIX"]
    try:
        response = logs.describe_log_streams(
            logGroupName=LOG_GROUP_SIM,
            logStreamNamePrefix=prefix,
        )
        streams = response.get("logStreams", [])
        if not streams:
            return "---"
        
        # Ordenar pelos mais recentes
        streams.sort(key=lambda x: x.get("lastEventTimestamp", 0), reverse=True)
        
        for s in streams[:2]:
            events = logs.get_log_events(
                logGroupName=LOG_GROUP_SIM,
                logStreamName=s["logStreamName"],
                limit=50,
            )
            for ev in reversed(events.get("events", [])):
                msg = ev["message"]
                if "[METRICS]" in msg:
                    # Regex ajustado para o formato real: [METRICS] P95=27423ms
                    match = re.search(r"P95=(\d+)ms", msg)
                    if match:
                        return f"{match.group(1)}ms"
    except Exception as e:
        pass
    return "---"


def run_command(cmd, description):
    print(f"\n>>> {description}")
    print(f"Executando: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True)
    return result


def update_sim_rate(new_rate):
    """
    Atualiza o RATE do sim_pedidos via Task Definition.
    Inspirado na lógica do dashboard_carga.py
    """
    print(f"\n>>> Atualizando taxa de pedidos para: {new_rate} ped/s")

    svc_name = SIM_CLIENTES_INFO["SERVICE_NAME"]
    family = SIM_CLIENTES_INFO["TASK_FAMILY"]
    container_name = SIM_CLIENTES_INFO["CONTAINER_NAME"]

    # 1. Pegar task definition atual
    response = ecs.describe_task_definition(taskDefinition=family)
    td = response["taskDefinition"]
    container_defs = td["containerDefinitions"]

    # 2. Atualizar env vars
    for cd in container_defs:
        if cd["name"] == container_name:
            env = cd.get("environment", [])
            # Atualizar ou adicionar RATE e AUTO_START
            found_rate = False
            found_auto = False
            for pair in env:
                if pair["name"] == "RATE":
                    pair["value"] = str(new_rate)
                    found_rate = True
                if pair["name"] == "AUTO_START":
                    pair["value"] = "true"
                    found_auto = True

            if not found_rate:
                env.append({"name": "RATE", "value": str(new_rate)})
            if not found_auto:
                env.append({"name": "AUTO_START", "value": "true"})
            cd["environment"] = env

    # 3. Registrar nova versão
    new_td = ecs.register_task_definition(
        family=family,
        taskRoleArn=td.get("taskRoleArn", ""),
        executionRoleArn=td.get("executionRoleArn", ""),
        networkMode=td.get("networkMode", "awsvpc"),
        containerDefinitions=container_defs,
        requiresCompatibilities=td.get("requiresCompatibilities", ["FARGATE"]),
        cpu=td.get("cpu", "512"),
        memory=td.get("memory", "1024"),
    )
    new_td_arn = new_td["taskDefinition"]["taskDefinitionArn"]

    # 4. Atualizar serviço e garantir 1 instância rodando
    ecs.update_service(
        cluster=CLUSTER_SIM,
        service=svc_name,
        taskDefinition=new_td_arn,
        desiredCount=1,
    )
    print(f"Serviço {svc_name} atualizado com nova TD (Rate={new_rate}).")


def get_running_instances():
    """Retorna a contagem de instâncias rodando para todos os serviços nos clusters."""
    with open(DEPLOY_OUTPUT_PATH, "r") as f:
        main_out = json.load(f)

    main_cluster = "dijkfood-cluster"
    services_main = [
        "dijkfood-cadastro-svc",
        "dijkfood-rotas-svc",
        "dijkfood-pedidos-svc",
    ]

    # Coleta todos os simuladores (Gateway, Clientes, Restaurante, Entregadores)
    services_sim = []
    for s_info in sim_config_data["SIMULATORS"].values():
        if s_info.get("SERVICE_NAME"):
            services_sim.append(s_info["SERVICE_NAME"])

    results = {}

    for svc in services_main:
        try:
            resp = ecs.describe_services(cluster=main_cluster, services=[svc])
            if resp["services"]:
                results[svc] = resp["services"][0]["runningCount"]
        except:
            results[svc] = 0

    for svc in services_sim:
        try:
            resp = ecs.describe_services(cluster=CLUSTER_SIM, services=[svc])
            if resp["services"]:
                results[svc] = resp["services"][0]["runningCount"]
        except:
            results[svc] = 0

    return results


def collect_p95_metrics(duration_s=30):
    """Extrai métricas P95 dos logs do CloudWatch durante uma janela de tempo."""
    print(f"Coletando métricas P95 por {duration_s} segundos")
    start_time = int(time.time() * 1000)
    time.sleep(duration_s)

    prefix = SIM_CLIENTES_INFO["LOG_STREAM_PREFIX"]

    p95_values = []
    try:
        streams = logs.describe_log_streams(
            logGroupName=LOG_GROUP_SIM,
            logStreamNamePrefix=prefix,
            orderBy="LastEventTime",
            descending=True,
            limit=5,
        )

        for stream in streams.get("logStreams", []):
            events = logs.get_log_events(
                logGroupName=LOG_GROUP_SIM,
                logStreamName=stream["logStreamName"],
                startTime=start_time,
                limit=100,
            )
            for ev in events.get("events", []):
                msg = ev["message"]
                match = re.search(r"P95=(\d+)ms", msg)
                if match:
                    p95_values.append(int(match.group(1)))
    except Exception as e:
        print(f"Erro ao ler logs: {e}")

    if p95_values:
        return sum(p95_values) / len(p95_values)
    return None


def main():
    print("=" * 60)
    print("INICIANDO BENCHMARK AUTOMATIZADO DIJKFOOD")
    print("=" * 60)


    scenarios = [10, 50, 200]
    final_results = []

    for rate in scenarios:
        print("\n" + "-" * 40)
        print(f"CENÁRIO: {rate} pedidos/segundo")
        print("-" * 40)

        # Ajustar Rate
        update_sim_rate(rate)

        # Esperar 5 minutos para estabilização (Requirement: "exatamente 10, 50, 200 req/s e 5 minutos de duração")
        print(
            "Aguardando 300 segundos (5 minutos) para observar a estabilização do cluster e Auto Scaling"
        )
        
        # Loop de monitoramento a cada 2 segundos
        start_wait = time.time()
        wait_duration = 300
        while time.time() - start_wait < wait_duration:
            elapsed = int(time.time() - start_wait)
            remaining = wait_duration - elapsed
            instances = get_running_instances()
            stats = get_order_stats()
            last_p95 = get_last_p95()
            
            # Formata a string de instâncias para uma linha compacta
            inst_str = " | ".join([f"{k.split('-')[-2] if '-' in k else k}: {v}" for k, v in instances.items()])
            
            # Formata a string de pedidos (Status)
            order_str = f"Conf: {stats['CONFIRMED']} | Prep: {stats['PREPARING']} | Pront: {stats['READY']} | Entreg: {stats['DELIVERED']}"

            print(f"\r[{elapsed:03d}s/{wait_duration}s] P95: {last_p95} | {order_str} | Inst: {inst_str}", end="", flush=True)
            time.sleep(2)
        print("\nPronto! Iniciando coleta de métricas P95 finais...")

        # Medir instâncias finais
        instances = get_running_instances()

        # Medir latência (Requirement: "API deve responder em menos de 500ms no percentil 95")
        p95_avg = collect_p95_metrics(60)

        result = {
            "scenario_rate_per_sec": rate,
            "latency_p95_ms": round(p95_avg, 2) if p95_avg else "N/A",
            "instances": instances,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        final_results.append(result)

        print(
            f"Resultado Cenário {rate}: P95={result['latency_p95_ms']}ms, Instâncias={sum(instances.values())}"
        )

    # Salvar resultados
    with open(BENCHMARK_RESULTS_PATH, "w") as f:
        json.dump(final_results, f, indent=4)

    print("\n" + "=" * 60)
    print(
        f"BENCHMARK CONCLUÍDO! Resultados salvos em: {BENCHMARK_RESULTS_PATH}"
    )
    print("=" * 60)

    # Exibir resumo detalhado por serviço
    print("\n" + "=" * 85)
    print(
        f"{'Cenário (p/s)':<15} | {'P95 Latency':<15} | {'Serviço':<30} | {'Instâncias':<10}"
    )
    print("-" * 85)
    for r in final_results:
        first_line = True
        for svc, count in r["instances"].items():
            scenario = (
                f"{r['scenario_rate_per_sec']} p/s" if first_line else ""
            )
            latency = f"{r['latency_p95_ms']}ms" if first_line else ""
            print(f"{scenario:<15} | {latency:<15} | {svc:<30} | {count:<10}")
            first_line = False
        print("-" * 85)


if __name__ == "__main__":
    main()
