"""
Dashboard Streamlit para controle e monitoramento dos simuladores DijkFood.

Permite:
  - Visualizar estado dos 4 services (general_api, sim_pedidos, sim_restaurante, sim_entregadores)
  - Escalar services individualmente (desiredCount)
  - Controlar o rate de pedidos do sim_pedidos (via redeploy de task definition)
  - Disparar tasks batch opcionais (sim_completo, carga_unitario)
  - Visualizar logs CloudWatch em tempo real, separados por simulador
  - Verificar health, métricas e configuração de rede do cluster
  - Monitorar P95 latency em tempo real

Uso:
    uv run streamlit run simulador_ecs/dashboard_carga.py
"""

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import boto3
import streamlit as st
from botocore.exceptions import ClientError

# =========================================================================
# CONFIGURAÇÃO
# =========================================================================
st.set_page_config(
    page_title="DijkFood — Simuladores",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CONFIG_PATH = CURRENT_DIR / "config.json"
DEPLOY_OUTPUT_PATH = ROOT_DIR / "deploy_output.json"
ALB_ENDPOINTS_PATH = ROOT_DIR / "alb_endpoints.json"
SIMULATOR_OUTPUT_PATH = CURRENT_DIR / "simulador_output.json"


@st.cache_data(ttl=60)
def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def load_api_url():
    """Carrega a URL do ALB da API a partir dos outputs."""
    if DEPLOY_OUTPUT_PATH.exists():
        with open(DEPLOY_OUTPUT_PATH, "r") as f:
            data = json.load(f)
            url = data.get("API_URL", "")
            if url:
                return url

    if ALB_ENDPOINTS_PATH.exists():
        with open(ALB_ENDPOINTS_PATH, "r") as f:
            data = json.load(f)
            return data.get("cadastro", "")

    return ""


def load_simulator_output():
    """Carrega simulador_output.json com dados de rede."""
    if SIMULATOR_OUTPUT_PATH.exists():
        with open(SIMULATOR_OUTPUT_PATH, "r") as f:
            return json.load(f)
    return {}


config = load_config()
sim_output = load_simulator_output()

AWS_REGION = config["AWS_REGION"]
CLUSTER_NAME = config["CLUSTER_NAME"]
LOG_GROUP_NAME = config["LOG_GROUP_NAME"]
SIMULATORS = config["SIMULATORS"]

# Clientes Boto3
ecs_client = boto3.client("ecs", region_name=AWS_REGION)
ec2_client = boto3.client("ec2", region_name=AWS_REGION)
logs_client = boto3.client("logs", region_name=AWS_REGION)

# Ícones por simulador
SIM_ICONS = {
    "general_api": "🌐",
    "sim_pedidos": "🛒",
    "sim_restaurante": "🍳",
    "sim_entregadores": "🏍️",
    "sim_completo": "⚡",
}


ACTIVE_ORDER_STATUSES = [
    "CONFIRMED",
    "PREPARING",
    "READY_FOR_PICKUP",
    "PICKED_UP",
    "IN_TRANSIT",
]


# =========================================================================
# HELPERS
# =========================================================================
def get_network_config():
    """Retorna SG ID e Subnets do cluster de simuladores."""
    sg_id = sim_output.get("SG_ID")
    subnet_ids = sim_output.get("SUBNET_IDS")

    if sg_id and subnet_ids:
        return sg_id, subnet_ids

    try:
        sgs = ec2_client.describe_security_groups(
            GroupNames=[config["SG_NAME"]]
        )
        sg_id = sgs["SecurityGroups"][0]["GroupId"]
    except Exception:
        sg_id = None

    try:
        vpcs = ec2_client.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        )
        vpc_id = vpcs["Vpcs"][0]["VpcId"]
        subnets = ec2_client.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )
        subnet_ids = [s["SubnetId"] for s in subnets["Subnets"][:2]]
    except Exception:
        subnet_ids = []

    return sg_id, subnet_ids


def list_running_tasks():
    """Lista tasks em execução no cluster de simuladores."""
    try:
        task_arns = ecs_client.list_tasks(cluster=CLUSTER_NAME)["taskArns"]
        if not task_arns:
            return []
        tasks = ecs_client.describe_tasks(
            cluster=CLUSTER_NAME, tasks=task_arns
        )["tasks"]
        return tasks
    except Exception:
        return []


def get_service_info(service_name: str):
    """Retorna info do service (desiredCount, runningCount, status)."""
    try:
        response = ecs_client.describe_services(
            cluster=CLUSTER_NAME, services=[service_name]
        )
        if (
            response["services"]
            and response["services"][0]["status"] != "INACTIVE"
        ):
            svc = response["services"][0]
            return {
                "status": svc["status"],
                "desiredCount": svc["desiredCount"],
                "runningCount": svc["runningCount"],
                "pendingCount": svc["pendingCount"],
            }
    except Exception:
        pass
    return None


def scale_service(service_name: str, task_family: str, desired_count: int):
    """Escala um ECS Service para o número desejado de instâncias."""
    try:
        ecs_client.update_service(
            cluster=CLUSTER_NAME,
            service=service_name,
            taskDefinition=task_family,
            desiredCount=desired_count,
        )
        return True
    except Exception as e:
        st.error(f"Erro ao escalar serviço: {e}")
        return False


def update_service_rate(
    service_name: str, task_family: str, container_name: str, new_rate: float
):
    """Atualiza o RATE no task definition e força novo deploy do service."""
    try:
        # 1. Buscar task definition atual
        td = ecs_client.describe_task_definition(taskDefinition=task_family)[
            "taskDefinition"
        ]

        # 2. Atualizar env vars RATE e AUTO_START no container
        for container in td["containerDefinitions"]:
            if container["name"] == container_name:
                env_list = container.get("environment", [])
                env_dict = {e["name"]: e["value"] for e in env_list}
                env_dict["RATE"] = str(new_rate)
                env_dict["AUTO_START"] = "true"
                container["environment"] = [
                    {"name": k, "value": v} for k, v in env_dict.items()
                ]

        # 3. Registrar nova revisão da task definition
        ecs_client.register_task_definition(
            family=task_family,
            networkMode=td["networkMode"],
            requiresCompatibilities=td["requiresCompatibilities"],
            cpu=td["cpu"],
            memory=td["memory"],
            executionRoleArn=td["executionRoleArn"],
            taskRoleArn=td.get("taskRoleArn", ""),
            containerDefinitions=td["containerDefinitions"],
        )

        # 4. Forçar novo deploy do service
        ecs_client.update_service(
            cluster=CLUSTER_NAME,
            service=service_name,
            taskDefinition=task_family,
            forceNewDeployment=True,
        )
        return True
    except Exception as e:
        st.error(f"Erro ao atualizar rate: {e}")
        return False


def run_ecs_task(
    sim_key: str,
    sim_config: dict,
    count: int,
    api_url: str,
    extra_env: list[dict] = None,
    cmd_override: list[str] = None,
):
    """Dispara N tasks do simulador especificado."""
    sg_id, subnet_ids = get_network_config()
    if not sg_id or not subnet_ids:
        st.error(
            "Não foi possível obter SG ID ou Subnets. Verifique se o deploy foi executado."
        )
        return None

    # Montar env vars
    env_mapping = sim_config.get("ENV_MAPPING", {})
    sim_alb_url = sim_output.get("SIM_ALB_URL", "")
    env_vars = []
    for key, value in env_mapping.items():
        resolved = value.replace("{API_URL}", api_url).replace(
            "{SIM_ALB_URL}", sim_alb_url
        )
        env_vars.append({"name": key, "value": resolved})
    if extra_env:
        env_vars.extend(extra_env)

    overrides = {
        "containerOverrides": [
            {
                "name": sim_config["CONTAINER_NAME"],
                "environment": env_vars,
            }
        ]
    }

    if cmd_override:
        overrides["containerOverrides"][0]["command"] = cmd_override

    response = ecs_client.run_task(
        cluster=CLUSTER_NAME,
        taskDefinition=sim_config["TASK_FAMILY"],
        count=count,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides=overrides,
    )
    return response


def stop_all_tasks():
    """Para todas as tasks em execução."""
    tasks = list_running_tasks()
    stopped = 0
    for task in tasks:
        try:
            ecs_client.stop_task(
                cluster=CLUSTER_NAME,
                task=task["taskArn"],
                reason="Parado via Dashboard",
            )
            stopped += 1
        except Exception:
            pass
    return stopped


def _http_get_json(url: str, timeout_s: float = 5.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read().decode("utf-8")
        return json.loads(data) if data else None


def get_active_orders(api_url: str, sim_alb_url: str = "") -> dict:
    """Obtém a contagem de pedidos ativos.

    Preferência: endpoint do general_api no ALB dos simuladores.
    Fallback: consulta direta no Order Service via ALB principal.
    """
    sim_alb_url = (sim_alb_url or "").rstrip("/")
    api_url = (api_url or "").rstrip("/")

    if sim_alb_url:
        try:
            res = _http_get_json(
                f"{sim_alb_url}/admin/active-orders", timeout_s=5.0
            )
            if isinstance(res, dict) and "total_active" in res:
                return res
        except Exception:
            pass

    by_status = {}
    total = 0
    if not api_url:
        return {"total_active": 0, "by_status": {}}

    for status in ACTIVE_ORDER_STATUSES:
        try:
            res = _http_get_json(
                f"{api_url}/pedidos/orders/status/{status}", timeout_s=5.0
            )
            count = len(res) if isinstance(res, list) else 0
        except Exception:
            count = 0
        by_status[status] = count
        total += count

    return {"total_active": total, "by_status": by_status}


def wait_for_no_active_orders(
    api_url: str, sim_alb_url: str = "", poll_s: float = 5.0
):
    placeholder = st.empty()
    while True:
        info = get_active_orders(api_url, sim_alb_url)
        total = int(info.get("total_active") or 0)
        by_status = info.get("by_status") or {}

        if total <= 0:
            placeholder.success(
                "✅ Nenhum pedido ativo. Pode parar os simuladores com segurança."
            )
            return

        details = " | ".join(
            f"{k}={int(v)}" for k, v in by_status.items() if int(v) > 0
        )
        placeholder.warning(
            f"⏳ Aguardando {total} pedido(s) ativo(s) finalizarem… {details}"
        )
        time.sleep(poll_s)


def graceful_scale_down(
    sim_key: str, sim_config: dict, *, api_url: str, sim_alb_url: str
):
    """Escala um simulador para 0 após drenar pedidos ativos."""
    # Para evitar loop infinito de pedidos chegando, tenta parar o sim_pedidos antes.
    sim_pedidos = SIMULATORS.get("sim_pedidos")
    if sim_pedidos:
        svc = get_service_info(sim_pedidos["SERVICE_NAME"])
        if svc and svc.get("desiredCount", 0) > 0:
            scale_service(
                sim_pedidos["SERVICE_NAME"], sim_pedidos["TASK_FAMILY"], 0
            )

    wait_for_no_active_orders(api_url, sim_alb_url)
    return scale_service(
        sim_config["SERVICE_NAME"], sim_config["TASK_FAMILY"], 0
    )


def fetch_logs(
    stream_prefix: str, limit_streams: int = 3, limit_events: int = 30
):
    """Busca logs do CloudWatch filtrados por stream prefix."""
    try:
        response = logs_client.describe_log_streams(
            logGroupName=LOG_GROUP_NAME, logStreamNamePrefix=stream_prefix
        )

        streams = response.get("logStreams", [])
        if not streams:
            return None

        # Ordenar os streams pelo último evento (mais recente primeiro)
        streams.sort(
            key=lambda x: x.get("lastEventTimestamp", 0), reverse=True
        )
        streams = streams[:limit_streams]

        all_logs = []
        for stream in streams:
            stream_name = stream["logStreamName"]
            events_resp = logs_client.get_log_events(
                logGroupName=LOG_GROUP_NAME,
                logStreamName=stream_name,
                limit=limit_events,
                startFromHead=False,
            )
            events = events_resp.get("events", [])
            all_logs.append({"stream": stream_name, "events": events})

        return all_logs

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def check_cluster_exists():
    """Verifica se o cluster de simuladores existe."""
    try:
        response = ecs_client.describe_clusters(clusters=[CLUSTER_NAME])
        clusters = response.get("clusters", [])
        for c in clusters:
            if c["status"] == "ACTIVE":
                return True
    except Exception:
        pass
    return False


def get_dynamo_counts(table_name="DijkfoodOrders"):
    """
    Usa GSI Query otimizada (não Scan) para contar pedidos por status.
    """
    try:
        dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)

        def count_gsi2pk(gsi2pk_value):
            response = dynamodb.query(
                TableName=table_name,
                IndexName="StatusIndex",
                Select="COUNT",
                KeyConditionExpression="GSI2PK = :gsi",
                ExpressionAttributeValues={":gsi": {"S": gsi2pk_value}},
            )
            total = response.get("Count", 0)
            while "LastEvaluatedKey" in response:
                response = dynamodb.query(
                    TableName=table_name,
                    IndexName="StatusIndex",
                    Select="COUNT",
                    KeyConditionExpression="GSI2PK = :gsi",
                    ExpressionAttributeValues={":gsi": {"S": gsi2pk_value}},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                total += response.get("Count", 0)
            return total

        counts = {
            "CONFIRMED": count_gsi2pk("STATUS#CONFIRMED"),
            "PREPARING": count_gsi2pk("STATUS#PREPARING"),
            "READY_FOR_PICKUP": count_gsi2pk("STATUS#READY_FOR_PICKUP"),
            "PICKED_UP": count_gsi2pk("STATUS#PICKED_UP"),
            "IN_TRANSIT": count_gsi2pk("STATUS#IN_TRANSIT"),
            "DELIVERED": count_gsi2pk("STATUS#DELIVERED"),
        }
        driver_counts = {
            "LIVRE": count_gsi2pk("DRIVER_STATUS#LIVRE"),
            "EM_ENTREGA": count_gsi2pk("DRIVER_STATUS#EM_ENTREGA"),
        }

        return counts, driver_counts
    except Exception as e:
        print(f"Erro no dynamodb query: {e}")
        return None, None


def extract_p95_from_logs():
    """Extrai a última métrica P95 dos logs do simulador de clientes."""
    try:
        sim_config = SIMULATORS.get("sim_pedidos", {})
        prefix = sim_config.get("LOG_STREAM_PREFIX", "sim-clientes")
        logs = fetch_logs(prefix, limit_streams=2, limit_events=50)
        if not logs:
            return None

        # Percorre os eventos mais recentes procurando [METRICS]
        for log_entry in logs:
            events = log_entry.get("events", [])
            for event in reversed(events):
                msg = event.get("message", "")
                if "[METRICS]" in msg:
                    # Parse: [METRICS] P95=123ms Avg=45ms | Rate=10/s | Sent=100 Err=2
                    result = {}
                    p95_match = re.search(r"P95=(\d+)", msg)
                    avg_match = re.search(r"Avg=(\d+)", msg)
                    rate_match = re.search(r"Rate=(\d+)", msg)
                    sent_match = re.search(r"Sent=(\d+)", msg)
                    err_match = re.search(r"Err=(\d+)", msg)

                    if p95_match:
                        result["p95"] = int(p95_match.group(1))
                    if avg_match:
                        result["avg"] = int(avg_match.group(1))
                    if rate_match:
                        result["rate"] = int(rate_match.group(1))
                    if sent_match:
                        result["sent"] = int(sent_match.group(1))
                    if err_match:
                        result["errors"] = int(err_match.group(1))

                    if result:
                        return result
        return None
    except Exception:
        return None


# =========================================================================
# CSS CUSTOMIZADO
# =========================================================================
st.markdown(
    """
<style>
    .stApp {
        background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
    }
    .sim-card {
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        backdrop-filter: blur(10px);
    }
    .status-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .badge-active { background: #00c853; color: #000; }
    .badge-inactive { background: #ff5252; color: #fff; }
    .badge-pending { background: #ffd740; color: #000; }
    div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 10px;
        padding: 1rem;
    }
    .p95-ok {
        background: linear-gradient(135deg, #00c853 0%, #00e676 100%);
        color: #000;
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        font-size: 1.5rem;
        font-weight: 700;
    }
    .p95-warn {
        background: linear-gradient(135deg, #ff6d00 0%, #ffd740 100%);
        color: #000;
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        font-size: 1.5rem;
        font-weight: 700;
    }
    .p95-bad {
        background: linear-gradient(135deg, #d50000 0%, #ff5252 100%);
        color: #fff;
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        font-size: 1.5rem;
        font-weight: 700;
    }
</style>
""",
    unsafe_allow_html=True,
)


# =========================================================================
# SIDEBAR
# =========================================================================
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/rocket.png", width=60)
    st.title("DijkFood Simuladores")
    st.markdown("---")

    # URL do ALB principal
    default_url = load_api_url()
    api_url = st.text_input(
        "🌐 URL Base APIs (ALB Principal)",
        value=default_url,
        help="Endereço do ALB das APIs de produção",
    )

    # URL do ALB interno dos simuladores
    sim_alb_url = sim_output.get("SIM_ALB_URL", "")
    if sim_alb_url:
        st.caption(f"🔗 ALB Sim: `{sim_alb_url}`")

    st.markdown("---")

    # Status do Cluster
    cluster_ok = check_cluster_exists()
    if cluster_ok:
        st.success("✅ Cluster ativo")
    else:
        st.error("❌ Cluster não encontrado")
        st.caption("Execute: `python simulador_ecs/deploy_simulador.py`")

    st.markdown("---")

    # Botão de Emergência
    if st.button(
        "🛑 Parar TODAS as Tasks", type="secondary", use_container_width=True
    ):
        with st.spinner(
            "Parando simuladores com drain (aguardando pedidos ativos)…"
        ):
            # 1) Para gerador de pedidos (para não entrar pedido novo)
            sim_pedidos_cfg = SIMULATORS.get("sim_pedidos")
            if sim_pedidos_cfg:
                try:
                    scale_service(
                        sim_pedidos_cfg["SERVICE_NAME"],
                        sim_pedidos_cfg["TASK_FAMILY"],
                        0,
                    )
                except Exception:
                    pass

            # 2) Aguarda pedidos ativos finalizarem no Order Service
            wait_for_no_active_orders(api_url, sim_alb_url)

            # 3) Agora sim derruba restaurante/entregadores (e opcionalmente o gateway)
            for key in ("sim_restaurante", "sim_entregadores", "general_api"):
                cfg = SIMULATORS.get(key)
                if not cfg:
                    continue
                try:
                    scale_service(cfg["SERVICE_NAME"], cfg["TASK_FAMILY"], 0)
                except Exception:
                    pass

            # 4) Por fim, para qualquer task avulsa que ainda esteja viva
            n = stop_all_tasks()
        st.info(f"✅ Drain concluído. {n} task(s) parada(s).")

    st.markdown("---")
    if st.button("🔄 Atualizar Dados", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# =========================================================================
# HEADER
# =========================================================================
st.markdown("# 🚀 Gerenciador de Simuladores DijkFood")
st.markdown(
    "Controle os simuladores, visualize logs em tempo real, e monitore a infraestrutura."
)

if not cluster_ok:
    st.warning(
        "⚠️ O cluster de simuladores não está ativo. Execute o deploy primeiro."
    )
    st.code("python simulador_ecs/deploy_simulador.py", language="bash")
    st.stop()

if not api_url:
    st.warning(
        "⚠️ URL do ALB principal não configurada. Os simuladores não saberão para onde enviar requisições."
    )


# =========================================================================
# TABS PRINCIPAIS
# =========================================================================
tab_control, tab_logs, tab_status = st.tabs(
    ["🎮 Controle", "📋 Logs", "📊 Status"]
)


# ─── ABA CONTROLE ────────────────────────────────────────────────────────
with tab_control:
    # ── P95 LATENCY BANNER ──
    st.markdown("### 📊 Métricas de Performance em Tempo Real")

    metrics = extract_p95_from_logs()
    if metrics:
        p95_val = metrics.get("p95", 0)
        avg_val = metrics.get("avg", 0)
        rate_val = metrics.get("rate", 0)
        sent_val = metrics.get("sent", 0)
        err_val = metrics.get("errors", 0)

        # P95 Banner colorido
        if p95_val <= 500:
            css_class = "p95-ok"
            emoji = "✅"
        elif p95_val <= 1000:
            css_class = "p95-warn"
            emoji = "⚠️"
        else:
            css_class = "p95-bad"
            emoji = "❌"

        st.markdown(
            f'<div class="{css_class}">'
            f"{emoji} P95 Latency: <strong>{p95_val}ms</strong> "
            f"(meta: ≤500ms)"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.markdown("")  # Spacer

        # Métricas em colunas
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(
            "P95 Latência",
            f"{p95_val}ms",
            delta=f"{'OK' if p95_val <= 500 else 'ALTO'}",
        )
        m2.metric("Avg Latência", f"{avg_val}ms")
        m3.metric("Rate Config.", f"{rate_val}/s")
        m4.metric("Total Enviados", f"{sent_val:,}")
        m5.metric("Total Erros", f"{err_val:,}")
    else:
        st.info(
            "📊 Métricas P95 aparecerão aqui quando o simulador de clientes estiver rodando."
        )

    st.markdown("---")

    # ── CONTROLE RÁPIDO DE RATE ──
    st.markdown("### ⚡ Controle Rápido de Carga")
    st.caption(
        "Configure a taxa de pedidos por segundo. O professor solicitou testes com 10, 50 e 200 ped/s."
    )

    rate_cols = st.columns(4)
    rate_presets = [
        ("🟢 10/s", 10),
        ("🟡 50/s", 50),
        ("🔴 200/s", 200),
    ]

    sim_pedidos_config = SIMULATORS.get("sim_pedidos", {})

    for idx, (label, rate_val) in enumerate(rate_presets):
        with rate_cols[idx]:
            if st.button(
                label, key=f"quick_rate_{rate_val}", use_container_width=True
            ):
                with st.spinner(f"Aplicando rate={rate_val}/s..."):
                    ok = update_service_rate(
                        sim_pedidos_config["SERVICE_NAME"],
                        sim_pedidos_config["TASK_FAMILY"],
                        sim_pedidos_config["CONTAINER_NAME"],
                        rate_val,
                    )
                    if ok:
                        # Garante que o serviço está rodando com pelo menos 1 instância
                        svc = get_service_info(
                            sim_pedidos_config["SERVICE_NAME"]
                        )
                        if svc and svc["desiredCount"] == 0:
                            scale_service(
                                sim_pedidos_config["SERVICE_NAME"],
                                sim_pedidos_config["TASK_FAMILY"],
                                1,
                            )
                        st.success(
                            f"✅ Rate atualizado para {rate_val}/s! Novo deploy em andamento."
                        )
                        time.sleep(2)
                        st.rerun()

    with rate_cols[3]:
        custom_rate = st.number_input(
            "Custom", min_value=1, max_value=500, value=10, key="custom_rate"
        )
        if st.button(
            "Aplicar", key="apply_custom_rate", use_container_width=True
        ):
            with st.spinner(f"Aplicando rate={custom_rate}/s..."):
                ok = update_service_rate(
                    sim_pedidos_config["SERVICE_NAME"],
                    sim_pedidos_config["TASK_FAMILY"],
                    sim_pedidos_config["CONTAINER_NAME"],
                    custom_rate,
                )
                if ok:
                    svc = get_service_info(sim_pedidos_config["SERVICE_NAME"])
                    if svc and svc["desiredCount"] == 0:
                        scale_service(
                            sim_pedidos_config["SERVICE_NAME"],
                            sim_pedidos_config["TASK_FAMILY"],
                            1,
                        )
                    st.success(f"✅ Rate atualizado para {custom_rate}/s!")
                    time.sleep(2)
                    st.rerun()

    st.markdown("---")

    # Listar tasks ativas
    running_tasks = list_running_tasks()
    if running_tasks:
        st.info(
            f"🟢 **{len(running_tasks)} task(s) em execução** no cluster de simuladores"
        )

    # ── SERVICES ──
    st.markdown("### 🔁 Services")

    services = {k: v for k, v in SIMULATORS.items() if v["TYPE"] == "service"}
    cols = st.columns(len(services))

    for idx, (sim_key, sim_config) in enumerate(services.items()):
        with cols[idx]:
            description = sim_config["DESCRIPTION"]
            service_name = sim_config["SERVICE_NAME"]
            icon = SIM_ICONS.get(sim_key, "🔁")
            short_desc = description.split("—")[0].strip()

            st.markdown(f"#### {icon} {short_desc}")

            # Status do service
            svc_info = get_service_info(service_name)
            if svc_info:
                mcol1, mcol2 = st.columns(2)
                with mcol1:
                    st.metric("Desejadas", svc_info["desiredCount"])
                with mcol2:
                    st.metric("Rodando", svc_info["runningCount"])

                if svc_info["pendingCount"] > 0:
                    st.caption(f"⏳ {svc_info['pendingCount']} pending")
            else:
                st.caption("Service não encontrado ou inativo.")

            # Formulário de escala
            with st.form(key=f"scale_{sim_key}"):
                current = svc_info["desiredCount"] if svc_info else 0

                desired = st.slider(
                    "Instâncias",
                    min_value=0,
                    max_value=10,
                    value=current,
                    key=f"desired_{sim_key}",
                )

                submitted = st.form_submit_button(
                    "📐 Aplicar Escala",
                    type="primary",
                    use_container_width=True,
                )

                if submitted:
                    with st.spinner("Aplicando mudanças..."):
                        if desired == 0 and sim_key in (
                            "sim_restaurante",
                            "sim_entregadores",
                        ):
                            ok = graceful_scale_down(
                                sim_key,
                                sim_config,
                                api_url=api_url,
                                sim_alb_url=sim_alb_url,
                            )
                        else:
                            ok = scale_service(
                                service_name,
                                sim_config["TASK_FAMILY"],
                                desired,
                            )
                        if ok:
                            st.success(
                                f"✅ Escalado para {desired} instância(s)"
                            )
                            time.sleep(1)
                            st.rerun()

    # ── TASKS BATCH (opcionais) ──
    tasks_batch = {k: v for k, v in SIMULATORS.items() if v["TYPE"] == "task"}
    if tasks_batch:
        st.markdown("---")
        st.markdown("### ⚡ Tasks Batch (População)")

        batch_cols = st.columns(len(tasks_batch))
        for idx, (sim_key, sim_config) in enumerate(tasks_batch.items()):
            with batch_cols[idx]:
                icon = SIM_ICONS.get(sim_key, "⚡")
                st.markdown(
                    f"#### {icon} {sim_config['DESCRIPTION'].split('—')[0].split('-')[0].strip()}"
                )

                with st.form(key=f"form_{sim_key}"):
                    num_tasks = st.slider(
                        "Tasks a disparar",
                        min_value=1,
                        max_value=10,
                        value=1,
                        step=1,
                        key=f"tasks_{sim_key}",
                    )

                    extra_env = []
                    cmd_override = None

                    submitted = st.form_submit_button(
                        "🚀 Disparar", type="primary", use_container_width=True
                    )

                    if submitted:
                        with st.spinner(f"Disparando {num_tasks} task(s)..."):
                            response = run_ecs_task(
                                sim_key,
                                sim_config,
                                num_tasks,
                                api_url,
                                extra_env,
                                cmd_override,
                            )
                            if response:
                                launched = len(response.get("tasks", []))
                                failures = response.get("failures", [])
                                if launched > 0:
                                    st.success(
                                        f"✅ {launched} task(s) disparadas!"
                                    )
                                if failures:
                                    st.warning(f"⚠️ {len(failures)} falha(s)")
                                    for f_item in failures:
                                        st.caption(
                                            f"Motivo: {f_item.get('reason', 'desconhecido')}"
                                        )


# ─── ABA LOGS ────────────────────────────────────────────────────────────
with tab_logs:
    st.markdown("### 📋 Monitoramento e Logs")
    st.caption(
        f"Acompanhe o estado do banco e os logs dos containers (CloudWatch: `{LOG_GROUP_NAME}`)"
    )

    log_tab_names = ["📈 Ciclo de Vida (DynamoDB)"]
    for sim_config in SIMULATORS.values():
        name = sim_config["DESCRIPTION"].split("(")[0].split("—")[0].strip()
        log_tab_names.append(name)

    log_tabs = st.tabs(log_tab_names)

    # Nova Aba de Tempo Real (DynamoDB)
    with log_tabs[0]:
        st.markdown("### 🟢 Ciclo de Vida dos Pedidos")

        col_auto, col_btn = st.columns([1, 4])
        with col_auto:
            auto_refresh = st.checkbox(
                "Auto-Atualizar (3s)",
                value=False,
                help="Atualiza a contagem quase em tempo real",
            )
        with col_btn:
            if st.button("🔄 Atualizar Contagens", key="refresh_dynamo"):
                pass

        counts, drivers = get_dynamo_counts()

        if counts is not None:
            # ── P95 Latência no topo ──
            st.markdown("#### 🎯 Performance")
            metrics_here = extract_p95_from_logs()
            if metrics_here:
                p95_v = metrics_here.get("p95", 0)
                if p95_v <= 500:
                    st.success(
                        f"✅ **P95 Latency: {p95_v}ms** — Dentro do objetivo (≤500ms)"
                    )
                elif p95_v <= 1000:
                    st.warning(
                        f"⚠️ **P95 Latency: {p95_v}ms** — Acima do objetivo (≤500ms)"
                    )
                else:
                    st.error(
                        f"❌ **P95 Latency: {p95_v}ms** — Muito acima do objetivo (≤500ms)"
                    )
            else:
                st.caption("_Aguardando métricas do simulador..._")

            st.markdown("#### 📦 Status dos Pedidos")

            # Progresso visual
            total_orders = sum(counts.values())
            if total_orders > 0:
                progress_cols = st.columns(6)
                labels = [
                    "Confirmados",
                    "Em Preparo",
                    "Prontos",
                    "Coletados",
                    "Em Trânsito",
                    "Entregues",
                ]
                keys = [
                    "CONFIRMED",
                    "PREPARING",
                    "READY_FOR_PICKUP",
                    "PICKED_UP",
                    "IN_TRANSIT",
                    "DELIVERED",
                ]
                for pc, lbl, key in zip(progress_cols, labels, keys):
                    pc.metric(lbl, counts[key])

                # Barra de progresso mostrando o fluxo
                delivered_pct = (
                    counts["DELIVERED"] / total_orders
                    if total_orders > 0
                    else 0
                )
                st.progress(
                    delivered_pct,
                    text=f"Progresso: {counts['DELIVERED']}/{total_orders} entregues ({delivered_pct:.0%})",
                )

            else:
                c1, c2, c3, c4, c5, c6 = st.columns(6)
                c1.metric("Confirmados", 0)
                c2.metric("Em Preparo", 0)
                c3.metric("Prontos", 0)
                c4.metric("Coletados", 0)
                c5.metric("Em Trânsito", 0)
                c6.metric("Entregues", 0)

            st.markdown("#### 🏍️ Frota de Entregadores")
            d1, d2, d3 = st.columns(3)
            d1.metric("Livres", drivers["LIVRE"])
            d2.metric("Ocupados", drivers["EM_ENTREGA"])
            d3.metric("Total", drivers["LIVRE"] + drivers["EM_ENTREGA"])
        else:
            st.warning(
                "Não foi possível acessar a tabela DijkfoodOrders no momento."
            )

        if auto_refresh:
            time.sleep(3)
            st.rerun()

    for log_tab, (sim_key, sim_config) in zip(
        log_tabs[1:], SIMULATORS.items()
    ):
        with log_tab:
            prefix = sim_config["LOG_STREAM_PREFIX"]
            col_refresh, col_count = st.columns([1, 3])

            with col_refresh:
                if st.button("🔄 Atualizar", key=f"refresh_logs_{sim_key}"):
                    pass  # Streamlit reruns on button click

            with col_count:
                num_events = st.slider(
                    "Linhas por stream",
                    10,
                    100,
                    30,
                    10,
                    key=f"log_lines_{sim_key}",
                )

            logs_data = fetch_logs(
                prefix, limit_streams=5, limit_events=num_events
            )

            if logs_data is None:
                st.info(
                    "Nenhum log encontrado para este simulador. Dispare uma task ou escale o service primeiro."
                )
            else:
                # Agregar todos os eventos de todos os streams
                all_aggregated_events = []
                for log_entry in logs_data:
                    all_aggregated_events.extend(log_entry["events"])

                # Ordenar cronologicamente pelo timestamp
                all_aggregated_events.sort(key=lambda e: e.get("timestamp", 0))

                if not all_aggregated_events:
                    st.caption("Nenhum evento recente encontrado.")
                else:
                    log_text = ""
                    for event in all_aggregated_events:
                        ts = event.get("timestamp", 0)
                        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                        msg = event.get("message", "")
                        log_text += f"[{dt.strftime('%H:%M:%S')}] {msg}\n"

                    st.code(log_text, language="log")


# ─── ABA STATUS ──────────────────────────────────────────────────────────
with tab_status:
    st.markdown("### 📊 Status do Cluster")

    # Métricas gerais
    running_tasks = list_running_tasks()
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("🖥️ Tasks Ativas", len(running_tasks))
    with col2:
        services_count = sum(
            1 for s in SIMULATORS.values() if s["TYPE"] == "service"
        )
        st.metric("🔁 Services", services_count)
    with col3:
        task_types = sum(1 for s in SIMULATORS.values() if s["TYPE"] == "task")
        st.metric("⚡ Tasks Batch", task_types)
    with col4:
        st.metric("📊 Total Configs", len(SIMULATORS))

    st.markdown("---")

    # Status de cada service
    st.markdown("#### Status dos Services")
    service_items = [
        (k, v) for k, v in SIMULATORS.items() if v["TYPE"] == "service"
    ]
    svc_cols = st.columns(len(service_items))

    for idx, (sim_key, sim_config) in enumerate(service_items):
        with svc_cols[idx]:
            svc_info = get_service_info(sim_config["SERVICE_NAME"])
            running = svc_info["runningCount"] if svc_info else 0
            desired = svc_info["desiredCount"] if svc_info else 0
            status = (
                "ACTIVE"
                if running > 0
                else ("SCALING" if desired > 0 else "STOPPED")
            )

            badge_cls = {
                "ACTIVE": "badge-active",
                "SCALING": "badge-pending",
                "STOPPED": "badge-inactive",
            }.get(status, "badge-inactive")

            icon = SIM_ICONS.get(sim_key, "🔁")
            short_desc = (
                sim_config["DESCRIPTION"].split("—")[0].split("(")[0].strip()
            )

            st.markdown(
                f'<div class="sim-card">'
                f'<span class="status-badge {badge_cls}">{status}</span><br>'
                f"{icon} <strong>{short_desc}</strong><br>"
                f"<small>Running: {running}/{desired}</small>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # Detalhes das tasks em execução
    if running_tasks:
        st.markdown("#### Tasks em Execução")
        for task in running_tasks:
            task_id = task["taskArn"].split("/")[-1][:12]
            status = task.get("lastStatus", "UNKNOWN")
            task_def = task.get("taskDefinitionArn", "").split("/")[-1]
            started = task.get("startedAt")

            # Determinar qual simulador é
            sim_name = "desconhecido"
            sim_icon = "❓"
            for sk, sc in SIMULATORS.items():
                if sc["TASK_FAMILY"] in task.get("taskDefinitionArn", ""):
                    sim_name = (
                        sc["DESCRIPTION"].split("(")[0].split("—")[0].strip()
                    )
                    sim_icon = SIM_ICONS.get(sk, "🔁")
                    break

            badge_cls = {
                "RUNNING": "badge-active",
                "PENDING": "badge-pending",
                "PROVISIONING": "badge-pending",
            }.get(status, "badge-inactive")

            uptime = ""
            if started:
                delta = datetime.now(timezone.utc) - started
                minutes = int(delta.total_seconds() / 60)
                uptime = f" • {minutes}min"

            st.markdown(
                f'<div class="sim-card">'
                f'<span class="status-badge {badge_cls}">{status}</span> '
                f"{sim_icon} <strong>{sim_name}</strong> "
                f"<code>{task_id}</code>{uptime}"
                f"<br><small>{task_def}</small>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.info("Nenhuma task em execução no momento.")

    st.markdown("---")

    # Info de rede e configuração
    st.markdown("#### Configuração de Rede")
    sg_id, subnet_ids = get_network_config()

    col_net1, col_net2 = st.columns(2)
    with col_net1:
        st.text_input("Security Group ID", value=sg_id or "N/A", disabled=True)
        st.text_input("Cluster", value=CLUSTER_NAME, disabled=True)
        st.text_input(
            "ALB Simuladores",
            value=sim_output.get("SIM_ALB_URL", "N/A"),
            disabled=True,
        )
    with col_net2:
        st.text_input(
            "Subnets",
            value=", ".join(subnet_ids) if subnet_ids else "N/A",
            disabled=True,
        )
        st.text_input("Log Group", value=LOG_GROUP_NAME, disabled=True)
        st.text_input("ALB Principal", value=api_url or "N/A", disabled=True)

    st.markdown("---")
    st.markdown("#### Simuladores Registrados")
    for sim_key, sim_config in SIMULATORS.items():
        icon = SIM_ICONS.get(
            sim_key, "🔁" if sim_config["TYPE"] == "service" else "⚡"
        )
        with st.expander(f"{icon} {sim_config['DESCRIPTION']}"):
            st.json(
                {
                    "key": sim_key,
                    "repo": sim_config["REPO_NAME"],
                    "task_family": sim_config["TASK_FAMILY"],
                    "type": sim_config["TYPE"],
                    "dockerfile": sim_config["DOCKERFILE"],
                    "env_mapping": sim_config.get("ENV_MAPPING", {}),
                }
            )
