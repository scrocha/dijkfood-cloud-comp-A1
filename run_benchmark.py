import argparse
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import boto3

# Configurações de Caminhos
ROOT_DIR = Path(__file__).resolve().parent
DEPLOY_OUTPUT_PATH = ROOT_DIR / "deploy_output.json"
SIMULATOR_OUTPUT_PATH = ROOT_DIR / "simulador_ecs" / "simulador_output.json"
SIM_CONFIG_PATH = ROOT_DIR / "simulador_ecs" / "config.json"
BENCHMARK_RESULTS_PATH = ROOT_DIR / "benchmark_results.json"

# Carregar Configurações do Simulador
with open(SIM_CONFIG_PATH, "r") as f:
    sim_config_data = json.load(f)

AWS_REGION = sim_config_data["AWS_REGION"]
CLUSTER_SIM = sim_config_data["CLUSTER_NAME"]
CLUSTER_MAIN = sim_config_data.get("MAIN_CLUSTER_NAME", "dijkfood-cluster")
LOG_GROUP_SIM = sim_config_data["LOG_GROUP_NAME"]
GENERAL_API_INFO = sim_config_data["SIMULATORS"]["general_api"]
SIM_CLIENTES_INFO = sim_config_data["SIMULATORS"]["sim_pedidos"]
MAIN_SERVICES = sim_config_data.get("MAIN_SERVICES", {})

# Clientes Boto3
ecs = boto3.client("ecs", region_name=AWS_REGION)
logs = boto3.client("logs", region_name=AWS_REGION)


def run_command(cmd, description):
    print(f"\n>>> {description}")
    print(f"Executando: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True)
    return result


def load_json_file(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def artifacts_ready() -> bool:
    deploy_data = load_json_file(DEPLOY_OUTPUT_PATH)
    sim_data = load_json_file(SIMULATOR_OUTPUT_PATH)

    required_deploy_keys = {
        "API_URL",
        "ALB_DNS",
        "SG_ID",
        "VPC_ID",
        "SUBNET_IDS",
    }
    required_sim_keys = {
        "CLUSTER_NAME",
        "SG_ID",
        "VPC_ID",
        "SUBNET_IDS",
        "SIM_ALB_DNS",
        "SIM_ALB_URL",
        "SIMULATORS",
    }

    return required_deploy_keys.issubset(
        deploy_data
    ) and required_sim_keys.issubset(sim_data)


def ensure_deployment(force: bool = False):
    if not force and artifacts_ready():
        print(
            "\n>>> Artefatos de deploy já disponíveis. Reutilizando ambiente existente."
        )
        return

    try:
        run_command(
            [sys.executable, "deploy.py"],
            "Deploy completo da infraestrutura e dos simuladores",
        )
    except subprocess.CalledProcessError as exc:
        if _environment_looks_alive():
            print(
                "\n>>> Deploy falhou, mas o ambiente ECS já está vivo. "
                "Continuando o benchmark com os recursos existentes."
            )
            return
        raise RuntimeError(
            "Falha no deploy e não foi possível confirmar um ambiente ativo."
        ) from exc


def _environment_looks_alive() -> bool:
    try:
        instances = get_running_instances()
        return any(count > 0 for count in instances.values())
    except Exception:
        return False


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


def fetch_logs(log_group_name, stream_prefix, start_time_ms):
    """Busca eventos do CloudWatch por janela de tempo e prefixo de stream."""
    events = []
    next_token = None

    while True:
        kwargs = {
            "logGroupName": log_group_name,
            "logStreamNamePrefix": stream_prefix,
            "startTime": start_time_ms,
            "interleaved": True,
        }
        if next_token:
            kwargs["nextToken"] = next_token

        response = logs.filter_log_events(**kwargs)
        events.extend(response.get("events", []))
        new_token = response.get("nextToken")
        if not new_token or new_token == next_token:
            break
        next_token = new_token

    return [{"stream": stream_prefix, "events": events}] if events else None


def collect_log_events(log_group_name, stream_prefix, start_time_ms):
    """Busca eventos recentes do CloudWatch dentro da janela do benchmark."""
    try:
        return fetch_logs(log_group_name, stream_prefix, start_time_ms)
    except Exception as e:
        print(f"Erro ao buscar logs de {stream_prefix}: {e}")
        return None


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _empty_stats():
    return {"durations_ms": [], "total": 0, "failures": 0}


def _finalize_stats(stats):
    durations = stats["durations_ms"]
    total = stats["total"]
    failures = stats["failures"]
    return {
        "count": total,
        "avg_ms": sum(durations) / len(durations) if durations else None,
        "p95_ms": _percentile(durations, 95),
        "failures": failures,
        "failure_rate": (failures / total) if total else 0.0,
    }


def _parse_general_api_logs(logs_data, start_time_ms):
    aggregate = _empty_stats()
    by_endpoint = defaultdict(_empty_stats)
    by_checkout_step = defaultdict(_empty_stats)
    by_downstream_service = defaultdict(_empty_stats)

    api_request_re = re.compile(
        r"api_request method=(\S+) path=(\S+) status=(\d+) duration_ms=([\d.]+)"
    )
    checkout_step_re = re.compile(
        r"checkout_step trace=(\S+) step=(\S+) status=(\S+) duration_ms=([\d.]+)"
    )
    downstream_call_re = re.compile(
        r"downstream_call service=(\S+) method=(\S+) url=(\S+) status=(\S+|error) duration_ms=([\d.]+)"
    )

    for log_entry in logs_data or []:
        for ev in log_entry.get("events", []):
            if ev.get("timestamp", 0) < start_time_ms:
                continue

            msg = ev.get("message", "")

            match = api_request_re.search(msg)
            if match:
                method = match.group(1)
                path = match.group(2)
                status = int(match.group(3))
                duration_ms = float(match.group(4))
                _update_stats(aggregate, duration_ms, status)
                _update_stats(
                    by_endpoint[f"{method} {path}"], duration_ms, status
                )
                continue

            match = checkout_step_re.search(msg)
            if match:
                step = match.group(2)
                status = match.group(3)
                duration_ms = float(match.group(4))
                _update_stats(
                    by_checkout_step[step],
                    duration_ms,
                    500 if status != "ok" else 200,
                )
                continue

            match = downstream_call_re.search(msg)
            if match:
                service = match.group(1)
                status = match.group(4)
                duration_ms = float(match.group(5))
                http_status = 500 if status == "error" else int(status)
                _update_stats(
                    by_downstream_service[service], duration_ms, http_status
                )

    return {
        "aggregate": _finalize_stats(aggregate),
        "by_endpoint": {k: _finalize_stats(v) for k, v in by_endpoint.items()},
        "by_checkout_step": {
            k: _finalize_stats(v) for k, v in by_checkout_step.items()
        },
        "by_downstream_service": {
            k: _finalize_stats(v) for k, v in by_downstream_service.items()
        },
    }


def _update_stats(stats, duration_ms, status_code):
    stats["durations_ms"].append(duration_ms)
    stats["total"] += 1
    if status_code >= 400:
        stats["failures"] += 1


def _parse_simulator_metrics(logs_data, start_time_ms):
    result = _empty_stats()
    metrics_re = re.compile(
        r"\[METRICS\] P95=(\d+)ms Avg=(\d+)ms \| Rate=(\d+(?:\.\d+)?)/s \| Sent=(\d+) Err=(\d+)"
    )
    for log_entry in logs_data or []:
        for ev in log_entry.get("events", []):
            if ev.get("timestamp", 0) < start_time_ms:
                continue
            msg = ev.get("message", "")
            match = metrics_re.search(msg)
            if not match:
                continue
            p95_ms = float(match.group(1))
            avg_ms = float(match.group(2))
            sent = int(match.group(4))
            err = int(match.group(5))
            _update_stats(result, p95_ms, 500 if err > 0 else 200)
            result.setdefault("reported_p95_ms", []).append(p95_ms)
            result.setdefault("reported_avg_ms", []).append(avg_ms)
            result.setdefault("sent", 0)
            result.setdefault("errors", 0)
            result["sent"] += sent
            result["errors"] += err
    return result


def get_running_instances():
    """Retorna a contagem de instâncias rodando para todos os serviços nos clusters."""
    service_inventory = []

    for service_info in MAIN_SERVICES.values():
        service_name = service_info.get("SERVICE_NAME")
        if service_name:
            service_inventory.append((CLUSTER_MAIN, service_name))

    # Coleta todos os simuladores do cluster dedicado
    for s_info in sim_config_data["SIMULATORS"].values():
        service_name = s_info.get("SERVICE_NAME")
        if service_name:
            service_inventory.append((CLUSTER_SIM, service_name))

    results = {}

    for cluster_name, service_name in service_inventory:
        try:
            resp = ecs.describe_services(
                cluster=cluster_name, services=[service_name]
            )
            services = resp.get("services", [])
            if services:
                results[service_name] = services[0].get("runningCount", 0)
            else:
                results[service_name] = 0
        except Exception:
            results[service_name] = 0

    return results


def collect_endpoint_metrics(duration_s=30):
    """Extrai métricas por endpoint da API geral e do simulador de pedidos."""
    print(f"Coletando métricas por endpoint por {duration_s} segundos")
    start_time = int(time.time() * 1000)
    time.sleep(duration_s)

    general_logs = collect_log_events(
        LOG_GROUP_SIM, GENERAL_API_INFO["LOG_STREAM_PREFIX"], start_time
    )
    simulator_logs = collect_log_events(
        LOG_GROUP_SIM, SIM_CLIENTES_INFO["LOG_STREAM_PREFIX"], start_time
    )

    general_metrics = _parse_general_api_logs(general_logs, start_time)
    simulator_metrics = _parse_simulator_metrics(simulator_logs, start_time)

    checkout_metrics = general_metrics["by_endpoint"].get("POST /checkout")

    return {
        "aggregate": general_metrics["aggregate"],
        "checkout": checkout_metrics,
        "by_endpoint": general_metrics["by_endpoint"],
        "checkout_steps": general_metrics["by_checkout_step"],
        "downstream_services": general_metrics["by_downstream_service"],
        "simulator_metrics": simulator_metrics,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Executa benchmark dos simuladores DijkFood"
    )
    parser.add_argument(
        "--force-deploy",
        action="store_true",
        help="Força o redeploy completo antes do benchmark",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("INICIANDO BENCHMARK AUTOMATIZADO DIJKFOOD")
    print("=" * 60)

    # 1. Reutiliza o ambiente quando já houver artefatos válidos.
    ensure_deployment(force=args.force_deploy)

    scenarios = [10, 50, 200]
    final_results = []

    for rate in scenarios:
        print("\n" + "-" * 40)
        print(f"CENÁRIO: {rate} pedidos/segundo")
        print("-" * 40)

        # Ajustar Rate
        update_sim_rate(rate)

        # Esperar 60s para estabilização (Requirement: "espere 1 minuto para que o alb e o cluster estabilizem")
        print(
            "Aguardando 60 segundos para estabilização do cluster e Auto Scaling"
        )
        time.sleep(60)

        # Medir instâncias
        instances = get_running_instances()

        # Medir desempenho por endpoint da API geral
        request_metrics = collect_endpoint_metrics(30)

        result = {
            "scenario_rate_per_sec": rate,
            "request_metrics": request_metrics,
            "instances": instances,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        final_results.append(result)

        aggregate = (
            request_metrics.get("aggregate", {}) if request_metrics else {}
        )
        agg_p95 = aggregate.get("p95_ms")
        agg_avg = aggregate.get("avg_ms")
        print(
            f"Resultado Cenário {rate}: P95 agregado={agg_p95:.2f}ms, "
            f"Avg agregado={agg_avg:.2f}ms, Instâncias={sum(instances.values())}"
            if agg_p95 is not None and agg_avg is not None
            else f"Resultado Cenário {rate}: Instâncias={sum(instances.values())}"
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
        f"{'Cenário (p/s)':<15} | {'Tipo':<14} | {'Alvo':<30} | {'Avg':<10} | {'P95':<10} | {'Falhas':<12}"
    )
    print("-" * 85)
    for r in final_results:
        request_metrics = r.get("request_metrics", {})
        aggregate = request_metrics.get("aggregate") or {}
        checkout = request_metrics.get("checkout") or {}
        endpoint_metrics = request_metrics.get("by_endpoint", {})
        checkout_steps = request_metrics.get("checkout_steps", {})
        downstream_services = request_metrics.get("downstream_services", {})
        simulator_metrics = request_metrics.get("simulator_metrics", {})

        first_line = True

        def print_row(row_type: str, target: str, metrics: dict):
            scenario = (
                f"{r['scenario_rate_per_sec']} p/s" if first_line else ""
            )
            avg = (
                f"{metrics['avg_ms']:.2f}ms"
                if metrics.get("avg_ms") is not None
                else "N/A"
            )
            p95 = (
                f"{metrics['p95_ms']:.2f}ms"
                if metrics.get("p95_ms") is not None
                else "N/A"
            )
            failures = f"{metrics.get('failures', 0)}/{metrics.get('count', 0)} ({metrics.get('failure_rate', 0.0):.1%})"
            print(
                f"{scenario:<15} | {row_type:<12} | {target:<28} | {avg:<10} | {p95:<10} | {failures:<12}"
            )

        if aggregate:
            print_row("AGREGADO", "TODAS AS REQUISICOES", aggregate)
            first_line = False
        if checkout:
            print_row("PEDIDO", "POST /checkout", checkout)
            first_line = False
        for step, metrics in sorted(checkout_steps.items()):
            print_row("STEP", f"checkout:{step}", metrics)
            first_line = False
        for service, metrics in sorted(downstream_services.items()):
            print_row("DOWNSTREAM", service, metrics)
            first_line = False
        if simulator_metrics:
            sim_avg = simulator_metrics.get("reported_avg_ms") or []
            sim_p95 = simulator_metrics.get("reported_p95_ms") or []
            sim_summary = {
                "avg_ms": sum(sim_avg) / len(sim_avg) if sim_avg else None,
                "p95_ms": sum(sim_p95) / len(sim_p95) if sim_p95 else None,
                "count": simulator_metrics.get("sent", 0),
                "failures": simulator_metrics.get("errors", 0),
                "failure_rate": (
                    simulator_metrics.get("errors", 0)
                    / simulator_metrics.get("sent", 1)
                ),
            }
            print_row("SIMULADOR", "sim_pedidos [METRICS]", sim_summary)
            first_line = False
        for endpoint, metrics in sorted(endpoint_metrics.items()):
            if endpoint == "POST /checkout":
                continue
            print_row("ENDPOINT", endpoint, metrics)
            first_line = False
        print("-" * 85)


if __name__ == "__main__":
    main()
