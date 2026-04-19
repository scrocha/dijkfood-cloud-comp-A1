"""
Dashboard Streamlit para controle e monitoramento dos simuladores DijkFood.

Permite:
  - Visualizar estado dos 4 services (general_api, sim_pedidos, sim_restaurante, sim_entregadores)
  - Escalar services individualmente (desiredCount)
  - Controlar o rate de pedidos do sim_pedidos (via redeploy de task definition)
  - Disparar tasks batch opcionais (sim_completo, carga_unitario)
  - Visualizar logs CloudWatch em tempo real, separados por simulador
  - Verificar health, métricas e configuração de rede do cluster

Uso:
    uv run streamlit run simulador_ecs/dashboard_carga.py
"""

import streamlit as st
import boto3
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from botocore.exceptions import ClientError

# =========================================================================
# CONFIGURAÇÃO
# =========================================================================
st.set_page_config(
    page_title="DijkFood — Simuladores",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
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

AWS_REGION = config['AWS_REGION']
CLUSTER_NAME = config['CLUSTER_NAME']
LOG_GROUP_NAME = config['LOG_GROUP_NAME']
SIMULATORS = config['SIMULATORS']

# Clientes Boto3
ecs_client = boto3.client('ecs', region_name=AWS_REGION)
ec2_client = boto3.client('ec2', region_name=AWS_REGION)
logs_client = boto3.client('logs', region_name=AWS_REGION)

# Ícones por simulador
SIM_ICONS = {
    "general_api": "🌐",
    "sim_pedidos": "🛒",
    "sim_restaurante": "🍳",
    "sim_entregadores": "🏍️",
    "sim_completo": "⚡",
}


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
        sgs = ec2_client.describe_security_groups(GroupNames=[config['SG_NAME']])
        sg_id = sgs['SecurityGroups'][0]['GroupId']
    except Exception:
        sg_id = None

    try:
        vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
        vpc_id = vpcs['Vpcs'][0]['VpcId']
        subnets = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
        subnet_ids = [s['SubnetId'] for s in subnets['Subnets'][:2]]
    except Exception:
        subnet_ids = []

    return sg_id, subnet_ids


def list_running_tasks():
    """Lista tasks em execução no cluster de simuladores."""
    try:
        task_arns = ecs_client.list_tasks(cluster=CLUSTER_NAME)['taskArns']
        if not task_arns:
            return []
        tasks = ecs_client.describe_tasks(cluster=CLUSTER_NAME, tasks=task_arns)['tasks']
        return tasks
    except Exception:
        return []


def get_service_info(service_name: str):
    """Retorna info do service (desiredCount, runningCount, status)."""
    try:
        response = ecs_client.describe_services(cluster=CLUSTER_NAME, services=[service_name])
        if response['services'] and response['services'][0]['status'] != 'INACTIVE':
            svc = response['services'][0]
            return {
                "status": svc['status'],
                "desiredCount": svc['desiredCount'],
                "runningCount": svc['runningCount'],
                "pendingCount": svc['pendingCount'],
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
            desiredCount=desired_count
        )
        return True
    except Exception as e:
        st.error(f"Erro ao escalar serviço: {e}")
        return False


def update_service_rate(service_name: str, task_family: str,
                        container_name: str, new_rate: float):
    """Atualiza o RATE no task definition e força novo deploy do service."""
    try:
        # 1. Buscar task definition atual
        td = ecs_client.describe_task_definition(taskDefinition=task_family)['taskDefinition']

        # 2. Atualizar env vars RATE e AUTO_START no container
        for container in td['containerDefinitions']:
            if container['name'] == container_name:
                env_list = container.get('environment', [])
                env_dict = {e['name']: e['value'] for e in env_list}
                env_dict['RATE'] = str(new_rate)
                env_dict['AUTO_START'] = 'true'
                container['environment'] = [
                    {'name': k, 'value': v} for k, v in env_dict.items()
                ]

        # 3. Registrar nova revisão da task definition
        ecs_client.register_task_definition(
            family=task_family,
            networkMode=td['networkMode'],
            requiresCompatibilities=td['requiresCompatibilities'],
            cpu=td['cpu'],
            memory=td['memory'],
            executionRoleArn=td['executionRoleArn'],
            taskRoleArn=td.get('taskRoleArn', ''),
            containerDefinitions=td['containerDefinitions'],
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


def run_ecs_task(sim_key: str, sim_config: dict, count: int,
                 api_url: str, extra_env: list[dict] = None,
                 cmd_override: list[str] = None):
    """Dispara N tasks do simulador especificado."""
    sg_id, subnet_ids = get_network_config()
    if not sg_id or not subnet_ids:
        st.error("Não foi possível obter SG ID ou Subnets. Verifique se o deploy foi executado.")
        return None

    # Montar env vars
    env_mapping = sim_config.get("ENV_MAPPING", {})
    sim_alb_url = sim_output.get("SIM_ALB_URL", "")
    env_vars = []
    for key, value in env_mapping.items():
        resolved = value.replace("{API_URL}", api_url).replace("{SIM_ALB_URL}", sim_alb_url)
        env_vars.append({"name": key, "value": resolved})
    if extra_env:
        env_vars.extend(extra_env)

    overrides = {
        'containerOverrides': [{
            'name': sim_config['CONTAINER_NAME'],
            'environment': env_vars,
        }]
    }

    if cmd_override:
        overrides['containerOverrides'][0]['command'] = cmd_override

    response = ecs_client.run_task(
        cluster=CLUSTER_NAME,
        taskDefinition=sim_config['TASK_FAMILY'],
        count=count,
        launchType='FARGATE',
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': subnet_ids,
                'securityGroups': [sg_id],
                'assignPublicIp': 'ENABLED'
            }
        },
        overrides=overrides
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
                task=task['taskArn'],
                reason="Parado via Dashboard"
            )
            stopped += 1
        except Exception:
            pass
    return stopped


def fetch_logs(stream_prefix: str, limit_streams: int = 3, limit_events: int = 30):
    """Busca logs do CloudWatch filtrados por stream prefix."""
    try:
        # A API do CloudWatch não permite orderBy='LastEventTime' com logStreamNamePrefix.
        # Por isso buscamos os streams e ordenamos localmente.
        response = logs_client.describe_log_streams(
            logGroupName=LOG_GROUP_NAME,
            logStreamNamePrefix=stream_prefix
        )

        streams = response.get('logStreams', [])
        if not streams:
            return None

        # Ordenar os streams pelo último evento (mais recente primeiro)
        streams.sort(key=lambda x: x.get('lastEventTimestamp', 0), reverse=True)
        streams = streams[:limit_streams]

        all_logs = []
        for stream in streams:
            stream_name = stream['logStreamName']
            events_resp = logs_client.get_log_events(
                logGroupName=LOG_GROUP_NAME,
                logStreamName=stream_name,
                limit=limit_events,
                startFromHead=False
            )
            events = events_resp.get('events', [])
            all_logs.append({
                "stream": stream_name,
                "events": events
            })

        return all_logs

    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            return None
        raise


def check_cluster_exists():
    """Verifica se o cluster de simuladores existe."""
    try:
        response = ecs_client.describe_clusters(clusters=[CLUSTER_NAME])
        clusters = response.get('clusters', [])
        for c in clusters:
            if c['status'] == 'ACTIVE':
                return True
    except Exception:
        pass
    return False


def get_dynamo_counts(table_name="DijkfoodOrders"):
    """
    Usa SCAN com Filtros para garantir que os dados sejam contados corretamente,
    independente da integridade dos índices GSI.
    """
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        table = dynamodb.Table(table_name)
        
        # Scan completo para pegar tudo de uma vez
        response = table.scan()
        items = response.get('Items', [])
        
        # Continua o scan se houver paginação
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))

        counts = {
            "CONFIRMED": 0, "PREPARING": 0, "READY_FOR_PICKUP": 0, 
            "PICKED_UP": 0, "IN_TRANSIT": 0, "DELIVERED": 0
        }
        driver_counts = {"LIVRE": 0, "EM_ENTREGA": 0}

        for item in items:
            pk = item.get('PK', '')
            status = item.get('status', item.get('Status', ''))
            
            if pk.startswith('ORDER#'):
                if status in counts:
                    counts[status] += 1
            elif pk.startswith('DRIVER#'):
                if status in driver_counts:
                    driver_counts[status] += 1
                elif not status: # Fallback para motorista sem status definido
                    driver_counts["LIVRE"] += 1
                    
        return counts, driver_counts
    except Exception as e:
        return None, None



# =========================================================================
# CSS CUSTOMIZADO
# =========================================================================
st.markdown("""
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
</style>
""", unsafe_allow_html=True)


# =========================================================================
# SIDEBAR
# =========================================================================
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/rocket.png", width=60)
    st.title("DijkFood Simuladores")
    st.markdown("---")

    # URL do ALB principal
    default_url = load_api_url()
    api_url = st.text_input("🌐 URL Base APIs (ALB Principal)", value=default_url,
                             help="Endereço do ALB das APIs de produção")

    # URL do ALB interno dos simuladores
    sim_alb_url = sim_output.get("SIM_ALB_URL", "")
    if sim_alb_url:
        st.caption(f"🔗 ALB Sim: `{sim_alb_url}`")

    st.markdown("---")

    # Status do Cluster
    cluster_ok = check_cluster_exists()
    if cluster_ok:
        st.success(f"✅ Cluster ativo")
    else:
        st.error(f"❌ Cluster não encontrado")
        st.caption("Execute: `python simulador_ecs/deploy_simulador.py`")

    st.markdown("---")

    # Botão de Emergência
    if st.button("🛑 Parar TODAS as Tasks", type="secondary", use_container_width=True):
        n = stop_all_tasks()
        st.info(f"{n} task(s) parada(s).")

    st.markdown("---")
    if st.button("🔄 Atualizar Dados", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# =========================================================================
# HEADER
# =========================================================================
st.markdown("# 🚀 Gerenciador de Simuladores DijkFood")
st.markdown("Controle os simuladores, visualize logs em tempo real, e monitore a infraestrutura.")

if not cluster_ok:
    st.warning("⚠️ O cluster de simuladores não está ativo. Execute o deploy primeiro.")
    st.code("python simulador_ecs/deploy_simulador.py", language="bash")
    st.stop()

if not api_url:
    st.warning("⚠️ URL do ALB principal não configurada. Os simuladores não saberão para onde enviar requisições.")


# =========================================================================
# TABS PRINCIPAIS
# =========================================================================
tab_control, tab_logs, tab_status = st.tabs(["🎮 Controle", "📋 Logs", "📊 Status"])


# ─── ABA CONTROLE ────────────────────────────────────────────────────────
with tab_control:

    # Listar tasks ativas
    running_tasks = list_running_tasks()
    if running_tasks:
        st.info(f"🟢 **{len(running_tasks)} task(s) em execução** no cluster de simuladores")

    # ── SERVICES ──
    st.markdown("### 🔁 Services")

    services = {k: v for k, v in SIMULATORS.items() if v['TYPE'] == 'service'}
    cols = st.columns(len(services))

    for idx, (sim_key, sim_config) in enumerate(services.items()):
        with cols[idx]:
            description = sim_config['DESCRIPTION']
            service_name = sim_config['SERVICE_NAME']
            icon = SIM_ICONS.get(sim_key, "🔁")
            short_desc = description.split('—')[0].strip()

            st.markdown(f"#### {icon} {short_desc}")

            # Status do service
            svc_info = get_service_info(service_name)
            if svc_info:
                mcol1, mcol2 = st.columns(2)
                with mcol1:
                    st.metric("Desejadas", svc_info['desiredCount'])
                with mcol2:
                    st.metric("Rodando", svc_info['runningCount'])

                if svc_info['pendingCount'] > 0:
                    st.caption(f"⏳ {svc_info['pendingCount']} pending")
            else:
                st.caption("Service não encontrado ou inativo.")

            # Formulário de escala
            with st.form(key=f"scale_{sim_key}"):
                current = svc_info['desiredCount'] if svc_info else 0

                desired = st.slider(
                    "Instâncias",
                    min_value=0, max_value=10, value=current,
                    key=f"desired_{sim_key}"
                )

                # Para sim_pedidos: controle de rate
                rate = None
                if sim_key == "sim_pedidos":
                    rate = st.slider(
                        "Rate (pedidos/min)",
                        min_value=1, max_value=200, value=50, step=5,
                        key=f"rate_{sim_key}",
                        help="Pedidos por minuto por instância. Atualiza via redeploy."
                    )

                submitted = st.form_submit_button(
                    "📐 Aplicar",
                    type="primary",
                    use_container_width=True
                )

                if submitted:
                    with st.spinner("Aplicando mudanças..."):
                        # Se mudou o rate do sim_pedidos, atualizar task definition
                        if sim_key == "sim_pedidos" and rate is not None:
                            update_service_rate(
                                service_name,
                                sim_config['TASK_FAMILY'],
                                sim_config['CONTAINER_NAME'],
                                rate
                            )

                        # Escalar service
                        ok = scale_service(
                            service_name,
                            sim_config['TASK_FAMILY'],
                            desired
                        )
                        if ok:
                            st.success(f"✅ Escalado para {desired} instância(s)")
                            time.sleep(1)
                            st.rerun()

    # ── TASKS BATCH (opcionais) ──
    tasks_batch = {k: v for k, v in SIMULATORS.items() if v['TYPE'] == 'task'}
    if tasks_batch:
        st.markdown("---")
        st.markdown("### ⚡ Tasks Batch (População)")

        batch_cols = st.columns(len(tasks_batch))
        for idx, (sim_key, sim_config) in enumerate(tasks_batch.items()):
            with batch_cols[idx]:
                icon = SIM_ICONS.get(sim_key, "⚡")
                st.markdown(f"#### {icon} {sim_config['DESCRIPTION'].split('—')[0].split('-')[0].strip()}")

                with st.form(key=f"form_{sim_key}"):
                    num_tasks = st.slider(
                        "Tasks a disparar",
                        min_value=1, max_value=10, value=1, step=1,
                        key=f"tasks_{sim_key}"
                    )

                    extra_env = []
                    cmd_override = None

                    if sim_key == "sim_completo":
                        duration = st.slider(
                            "Duração (s)", 60, 600, 300, 30,
                            key=f"duration_{sim_key}"
                        )
                        workers = st.slider(
                            "Workers", 1, 20, 5, 1,
                            key=f"workers_{sim_key}"
                        )
                        extra_env = [
                            {"name": "RUN_DURATION_S", "value": str(duration)},
                            {"name": "NUM_WORKERS", "value": str(workers)},
                        ]

                    submitted = st.form_submit_button(
                        f"🚀 Disparar",
                        type="primary",
                        use_container_width=True
                    )

                    if submitted:
                        with st.spinner(f"Disparando {num_tasks} task(s)..."):
                            response = run_ecs_task(
                                sim_key, sim_config, num_tasks,
                                api_url, extra_env, cmd_override
                            )
                            if response:
                                launched = len(response.get('tasks', []))
                                failures = response.get('failures', [])
                                if launched > 0:
                                    st.success(f"✅ {launched} task(s) disparadas!")
                                if failures:
                                    st.warning(f"⚠️ {len(failures)} falha(s)")
                                    for f_item in failures:
                                        st.caption(f"Motivo: {f_item.get('reason', 'desconhecido')}")


# ─── ABA LOGS ────────────────────────────────────────────────────────────
with tab_logs:
    st.markdown("### 📋 Monitoramento e Logs")
    st.caption(f"Acompanhe o estado do banco e os logs dos containers (CloudWatch: `{LOG_GROUP_NAME}`)")

    log_tab_names = ["📈 Tempo Real (DynamoDB)"]
    for sim_config in SIMULATORS.values():
        name = sim_config['DESCRIPTION'].split('(')[0].split('—')[0].strip()
        log_tab_names.append(name)

    log_tabs = st.tabs(log_tab_names)

    # Nova Aba de Tempo Real (DynamoDB)
    with log_tabs[0]:
        st.markdown("### 🟢 Controle Ativo - DynamoDB Scan")
        
        col_auto, col_btn = st.columns([1, 4])
        with col_auto:
            auto_refresh = st.checkbox("Auto-Atualizar (2s)", value=False, help="Atualiza a contagem quase em tempo real")
        with col_btn:
            if st.button("🔄 Atualizar Contagens", key="refresh_dynamo"):
                pass
        
        counts, drivers = get_dynamo_counts()

        if counts is not None:
            st.markdown("#### 📦 Status dos Pedidos")
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("Confirmados", counts["CONFIRMED"])
            c2.metric("Em Preparo", counts["PREPARING"], help="Cozinha")
            c3.metric("Prontos", counts["READY_FOR_PICKUP"])
            c4.metric("Coletados", counts["PICKED_UP"])
            c5.metric("Em Trânsito", counts["IN_TRANSIT"], help="GPS Ativo")
            c6.metric("Entregues", counts["DELIVERED"], help="Finalizados")
            
            st.markdown("#### 🏍️ Frota de Entregadores")
            d1, d2, d3 = st.columns(3)
            d1.metric("Livres", drivers["LIVRE"])
            d2.metric("Ocupados", drivers["EM_ENTREGA"])
            d3.metric("Total", drivers["LIVRE"] + drivers["EM_ENTREGA"])
        else:
            st.warning("Não foi possível acessar a tabela DijkfoodOrders no momento.")

        if auto_refresh:
            time.sleep(2)
            st.rerun()

    for log_tab, (sim_key, sim_config) in zip(log_tabs[1:], SIMULATORS.items()):
        with log_tab:
            prefix = sim_config['LOG_STREAM_PREFIX']
            col_refresh, col_count = st.columns([1, 3])

            with col_refresh:
                if st.button(f"🔄 Atualizar", key=f"refresh_logs_{sim_key}"):
                    pass  # Streamlit reruns on button click

            with col_count:
                num_events = st.slider(
                    "Linhas por stream", 10, 100, 30, 10,
                    key=f"log_lines_{sim_key}"
                )

            logs_data = fetch_logs(prefix, limit_streams=5, limit_events=num_events)

            if logs_data is None:
                st.info("Nenhum log encontrado para este simulador. Dispare uma task ou escale o service primeiro.")
            else:
                # Agregar todos os eventos de todos os streams
                all_aggregated_events = []
                for log_entry in logs_data:
                    all_aggregated_events.extend(log_entry['events'])
                
                # Ordenar cronologicamente pelo timestamp
                all_aggregated_events.sort(key=lambda e: e.get('timestamp', 0))

                if not all_aggregated_events:
                    st.caption("Nenhum evento recente encontrado.")
                else:
                    log_text = ""
                    for event in all_aggregated_events:
                        ts = event.get('timestamp', 0)
                        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                        msg = event.get('message', '')
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
        services_count = sum(1 for s in SIMULATORS.values() if s['TYPE'] == 'service')
        st.metric("🔁 Services", services_count)
    with col3:
        task_types = sum(1 for s in SIMULATORS.values() if s['TYPE'] == 'task')
        st.metric("⚡ Tasks Batch", task_types)
    with col4:
        st.metric("📊 Total Configs", len(SIMULATORS))

    st.markdown("---")

    # Status de cada service
    st.markdown("#### Status dos Services")
    service_items = [(k, v) for k, v in SIMULATORS.items() if v['TYPE'] == 'service']
    svc_cols = st.columns(len(service_items))

    for idx, (sim_key, sim_config) in enumerate(service_items):
        with svc_cols[idx]:
            svc_info = get_service_info(sim_config['SERVICE_NAME'])
            running = svc_info['runningCount'] if svc_info else 0
            desired = svc_info['desiredCount'] if svc_info else 0
            status = "ACTIVE" if running > 0 else ("SCALING" if desired > 0 else "STOPPED")

            badge_cls = {
                'ACTIVE': 'badge-active',
                'SCALING': 'badge-pending',
                'STOPPED': 'badge-inactive',
            }.get(status, 'badge-inactive')

            icon = SIM_ICONS.get(sim_key, "🔁")
            short_desc = sim_config["DESCRIPTION"].split("—")[0].split("(")[0].strip()

            st.markdown(
                f'<div class="sim-card">'
                f'<span class="status-badge {badge_cls}">{status}</span><br>'
                f'{icon} <strong>{short_desc}</strong><br>'
                f'<small>Running: {running}/{desired}</small>'
                f'</div>',
                unsafe_allow_html=True
            )

    st.markdown("---")

    # Detalhes das tasks em execução
    if running_tasks:
        st.markdown("#### Tasks em Execução")
        for task in running_tasks:
            task_id = task['taskArn'].split('/')[-1][:12]
            status = task.get('lastStatus', 'UNKNOWN')
            task_def = task.get('taskDefinitionArn', '').split('/')[-1]
            started = task.get('startedAt')

            # Determinar qual simulador é
            sim_name = "desconhecido"
            sim_icon = "❓"
            for sk, sc in SIMULATORS.items():
                if sc['TASK_FAMILY'] in task.get('taskDefinitionArn', ''):
                    sim_name = sc['DESCRIPTION'].split('(')[0].split('—')[0].strip()
                    sim_icon = SIM_ICONS.get(sk, "🔁")
                    break

            badge_cls = {
                'RUNNING': 'badge-active',
                'PENDING': 'badge-pending',
                'PROVISIONING': 'badge-pending',
            }.get(status, 'badge-inactive')

            uptime = ""
            if started:
                delta = datetime.now(timezone.utc) - started
                minutes = int(delta.total_seconds() / 60)
                uptime = f" • {minutes}min"

            st.markdown(
                f'<div class="sim-card">'
                f'<span class="status-badge {badge_cls}">{status}</span> '
                f'{sim_icon} <strong>{sim_name}</strong> '
                f'<code>{task_id}</code>{uptime}'
                f'<br><small>{task_def}</small>'
                f'</div>',
                unsafe_allow_html=True
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
        st.text_input("ALB Simuladores", value=sim_output.get("SIM_ALB_URL", "N/A"), disabled=True)
    with col_net2:
        st.text_input("Subnets", value=", ".join(subnet_ids) if subnet_ids else "N/A", disabled=True)
        st.text_input("Log Group", value=LOG_GROUP_NAME, disabled=True)
        st.text_input("ALB Principal", value=api_url or "N/A", disabled=True)

    st.markdown("---")
    st.markdown("#### Simuladores Registrados")
    for sim_key, sim_config in SIMULATORS.items():
        icon = SIM_ICONS.get(sim_key, "🔁" if sim_config['TYPE'] == 'service' else "⚡")
        with st.expander(f"{icon} {sim_config['DESCRIPTION']}"):
            st.json({
                "key": sim_key,
                "repo": sim_config['REPO_NAME'],
                "task_family": sim_config['TASK_FAMILY'],
                "type": sim_config['TYPE'],
                "dockerfile": sim_config['DOCKERFILE'],
                "env_mapping": sim_config.get('ENV_MAPPING', {}),
            })
