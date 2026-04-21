"""
Deploy da infraestrutura ECS para o cluster de simuladores DijkFood.

Cria um cluster ECS Fargate dedicado com:
  - ALB interno para comunicação entre os 4 services
  - general_api (gateway orquestrador)
  - sim_pedidos (gerador de checkout com rate controlável)
  - sim_restaurante (simula preparo e webhook)
  - sim_entregadores (simula GPS e entregas)

Lê 'deploy_output.json' (gerado pelo deploy_infra.py principal) para obter
ALB DNS, SG ID, VPC e subnets do cluster de APIs.

Uso:
    python deploy_simuladores.py            # deploy
    python deploy_simuladores.py --destroy   # destrói tudo
"""

import boto3
import json
import subprocess
import base64
import argparse
import time
from botocore.exceptions import ClientError
from pathlib import Path

# =========================================================================
# CAMINHOS (AJUSTADOS PARA A RAIZ)
# =========================================================================
ROOT_DIR = Path(__file__).resolve().parent
SIMULADOR_ECS_DIR = ROOT_DIR / "simulador_ecs"
CONFIG_PATH = SIMULADOR_ECS_DIR / "config.json"
DEPLOY_OUTPUT_PATH = ROOT_DIR / "deploy_output.json"
ALB_ENDPOINTS_PATH = ROOT_DIR / "alb_endpoints.json"
SIMULATOR_OUTPUT_PATH = SIMULADOR_ECS_DIR / "simulador_output.json"

if not CONFIG_PATH.exists():
    print(f"ERRO: Arquivo de configuração não encontrado em {CONFIG_PATH}")
    exit(1)

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

AWS_REGION = config["AWS_REGION"]
CLUSTER_NAME = config["CLUSTER_NAME"]
SG_NAME = config["SG_NAME"]
LOG_GROUP_NAME = config["LOG_GROUP_NAME"]
DEFAULT_CPU = config["DEFAULT_CPU"]
DEFAULT_MEMORY = config["DEFAULT_MEMORY"]
SIM_ALB_NAME = config["SIM_ALB_NAME"]
SIMULATORS = config["SIMULATORS"]

# =========================================================================
# CLIENTES BOTO3
# =========================================================================
ecs_client = boto3.client('ecs', region_name=AWS_REGION)
ecr_client = boto3.client('ecr', region_name=AWS_REGION)
logs_client = boto3.client('logs', region_name=AWS_REGION)
ec2_client = boto3.client('ec2', region_name=AWS_REGION)
sts_client = boto3.client('sts', region_name=AWS_REGION)
elbv2_client = boto3.client('elbv2', region_name=AWS_REGION)


# =========================================================================
# LEITURA DO DEPLOY PRINCIPAL
# =========================================================================
def load_main_deploy_output() -> dict:
    """Lê deploy_output.json para obter dados de rede do cluster principal."""
    output = {}

    if DEPLOY_OUTPUT_PATH.exists():
        with open(DEPLOY_OUTPUT_PATH, "r") as f:
            output = json.load(f)

    # Fallback: tenta alb_endpoints.json para a URL
    if "API_URL" not in output and ALB_ENDPOINTS_PATH.exists():
        with open(ALB_ENDPOINTS_PATH, "r") as f:
            endpoints = json.load(f)
            url = endpoints.get("cadastro", "")
            if url:
                output["API_URL"] = url

    if "API_URL" not in output:
        print("AVISO: deploy_output.json não encontrado ou sem API_URL.")
        print("       Os simuladores não saberão o endereço do ALB principal.")
        print("       Execute 'python deploy_infra.py' primeiro.")
        output["API_URL"] = "http://localhost"

    return output


def resolve_env_vars(env_mapping: dict, deploy_output: dict,
                     sim_alb_url: str = "") -> list[dict]:
    """Resolve placeholders {API_URL} e {SIM_ALB_URL} nos env vars."""
    result = []
    for key, value in env_mapping.items():
        resolved = value
        resolved = resolved.replace("{API_URL}", deploy_output.get("API_URL", "http://localhost"))
        resolved = resolved.replace("{SIM_ALB_URL}", sim_alb_url)
        result.append({"name": key, "value": resolved})
    return result


# =========================================================================
# INFRAESTRUTURA
# =========================================================================
def setup_log_group():
    """Cria o CloudWatch Log Group compartilhado para todos os simuladores."""
    print(f"  Verificando Log Group: {LOG_GROUP_NAME}")
    try:
        logs_client.create_log_group(logGroupName=LOG_GROUP_NAME)
        print(f"  Log Group criado.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
            print(f"  Log Group já existe.")
        else:
            raise e


def get_vpc_and_subnets() -> tuple[str, list[str]]:
    """Obtém VPC ID e subnets da VPC padrão."""
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    subnets = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    subnet_ids = [s['SubnetId'] for s in subnets['Subnets'][:2]]
    return vpc_id, subnet_ids


def setup_security_group(vpc_id: str, main_sg_id: str | None = None) -> str:
    """Cria SG para simuladores e configura comunicação cross-cluster."""
    print(f"  Configurando Security Group: {SG_NAME}")

    sg_id = None
    try:
        sg_response = ec2_client.create_security_group(
            GroupName=SG_NAME,
            Description='Security Group para o cluster de simuladores DijkFood',
            VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        print(f"  SG '{SG_NAME}' criado (ID: {sg_id}).")
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidGroup.Duplicate':
            sgs = ec2_client.describe_security_groups(GroupNames=[SG_NAME])
            sg_id = sgs['SecurityGroups'][0]['GroupId']
            print(f"  SG '{SG_NAME}' já existe. ID: {sg_id}")
        else:
            raise e

    # Abrir portas para tráfego: ALB (80) + cada service
    ports = [
        (80, "ALB HTTP"),
        (8000, "General API"),
        (8005, "Sim Pedidos"),
        (8006, "Sim Restaurante"),
        (8007, "Sim Entregadores"),
    ]
    for port, label in ports:
        try:
            ec2_client.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{
                    'IpProtocol': 'tcp',
                    'FromPort': port,
                    'ToPort': port,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                }]
            )
            print(f"  Porta {port} ({label}) aberta.")
        except ClientError as e:
            if e.response['Error']['Code'] != 'InvalidPermission.Duplicate':
                print(f"  Aviso ao abrir porta {port}: {e}")

    # Se temos o SG do cluster principal, permitir que os simuladores acessem as APIs
    if main_sg_id:
        try:
            ec2_client.authorize_security_group_ingress(
                GroupId=main_sg_id,
                IpPermissions=[{
                    'IpProtocol': 'tcp',
                    'FromPort': 80,
                    'ToPort': 80,
                    'UserIdGroupPairs': [{'GroupId': sg_id}]
                }]
            )
            print(f"  SG principal ({main_sg_id}): ingress do SG simuladores na porta 80 (ALB).")
        except ClientError as e:
            if e.response['Error']['Code'] != 'InvalidPermission.Duplicate':
                print(f"  Aviso ao configurar cross-SG: {e}")

    return sg_id


def build_and_push_image(sim_key: str, sim_config: dict) -> str:
    """Build e push da imagem Docker para um simulador específico."""
    repo_name = sim_config["REPO_NAME"]
    dockerfile = sim_config["DOCKERFILE"]
    dockerfile_path = ROOT_DIR / dockerfile

    print(f"  [{sim_key}] Build & Push: {repo_name}")
    account_id = sts_client.get_caller_identity()["Account"]
    ecr_uri = f"{account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com/{repo_name}"

    # Criar repositório ECR
    try:
        ecr_client.create_repository(repositoryName=repo_name)
        print(f"  [{sim_key}] Repositório ECR criado: {repo_name}")
    except ClientError as e:
        if e.response['Error']['Code'] != 'RepositoryAlreadyExistsException':
            raise e
        print(f"  [{sim_key}] Repositório ECR já existe: {repo_name}")

    # Login no ECR
    auth_token = ecr_client.get_authorization_token()
    token = auth_token['authorizationData'][0]['authorizationToken']
    username, password = base64.b64decode(token).decode('utf-8').split(':')
    registry = auth_token['authorizationData'][0]['proxyEndpoint']

    subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", registry],
        input=password.encode('utf-8'),
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Build a partir da raiz do projeto
    print(f"  [{sim_key}] Construindo imagem...")
    subprocess.run(
        ["docker", "build", "-t", repo_name, "-f", str(dockerfile_path), str(ROOT_DIR)],
        check=True
    )
    subprocess.run(["docker", "tag", f"{repo_name}:latest", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "tag", f"{repo_name}:latest", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "push", f"{ecr_uri}:latest"], check=True)
    print(f"  [{sim_key}] Imagem enviada: {ecr_uri}:latest")

    return ecr_uri


def register_task_definition(sim_key: str, sim_config: dict, ecr_uri: str,
                              env_vars: list[dict], role_arn: str):
    """Registra a Task Definition para um simulador."""
    task_family = sim_config["TASK_FAMILY"]
    container_name = sim_config["CONTAINER_NAME"]
    log_prefix = sim_config["LOG_STREAM_PREFIX"]

    print(f"  [{sim_key}] Registrando Task Definition: {task_family}")

    container_def = {
        "name": container_name,
        "image": f"{ecr_uri}:latest",
        "essential": True,
        "environment": env_vars,
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": LOG_GROUP_NAME,
                "awslogs-region": AWS_REGION,
                "awslogs-stream-prefix": log_prefix,
                "awslogs-create-group": "true"
            }
        }
    }

    # Adicionar port mapping para services
    if "CONTAINER_PORT" in sim_config:
        container_def["portMappings"] = [{
            "containerPort": sim_config["CONTAINER_PORT"],
            "hostPort": sim_config["CONTAINER_PORT"]
        }]

    # Adicionar CMD se especificado
    if sim_config.get("DEFAULT_CMD"):
        container_def["command"] = sim_config["DEFAULT_CMD"]

    ecs_client.register_task_definition(
        family=task_family,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu=DEFAULT_CPU,
        memory=DEFAULT_MEMORY,
        executionRoleArn=role_arn,
        taskRoleArn=role_arn,
        containerDefinitions=[container_def]
    )
    print(f"  [{sim_key}] Task Definition registrada.")


# =========================================================================
# ALB INTERNO
# =========================================================================
def setup_internal_alb(vpc_id: str, subnet_ids: list[str],
                       sg_id: str) -> tuple[str, str, dict]:
    """
    Cria ALB interno com Target Groups e routing rules para os 4 services.
    """
    print(f"\n--- ALB Interno: {SIM_ALB_NAME} ---")

    # 1. Criar ALB
    try:
        alb_response = elbv2_client.create_load_balancer(
            Name=SIM_ALB_NAME,
            Subnets=subnet_ids,
            SecurityGroups=[sg_id],
            Scheme='internal',
            Type='application',
            IpAddressType='ipv4'
        )
        alb_arn = alb_response['LoadBalancers'][0]['LoadBalancerArn']
        alb_dns = alb_response['LoadBalancers'][0]['DNSName']
        print(f"  ALB criado: {alb_dns}")
    except ClientError as e:
        if e.response['Error']['Code'] == 'DuplicateLoadBalancerName':
            albs = elbv2_client.describe_load_balancers(Names=[SIM_ALB_NAME])
            alb_arn = albs['LoadBalancers'][0]['LoadBalancerArn']
            alb_dns = albs['LoadBalancers'][0]['DNSName']
            print(f"  ALB já existe: {alb_dns}")
        else:
            raise

    # Aguardar ALB ficar disponível
    print("  Aguardando ALB ficar disponível...")
    waiter = elbv2_client.get_waiter('load_balancer_available')
    waiter.wait(LoadBalancerArns=[alb_arn])
    print("  ALB disponível.")

    # 2. Criar Target Groups para cada service
    tg_arns = {}
    default_tg_arn = None

    for sim_key, sim_config in SIMULATORS.items():
        if sim_config["TYPE"] != "service":
            continue

        tg_name = sim_config["TG_NAME"]
        port = sim_config["CONTAINER_PORT"]
        health_path = sim_config.get("HEALTH_CHECK_PATH", "/health")

        try:
            tg_resp = elbv2_client.create_target_group(
                Name=tg_name,
                Protocol='HTTP',
                Port=port,
                VpcId=vpc_id,
                TargetType='ip',
                HealthCheckProtocol='HTTP',
                HealthCheckPath=health_path,
                HealthCheckIntervalSeconds=30,
                HealthyThresholdCount=2,
                UnhealthyThresholdCount=3,
            )
            tg_arn = tg_resp['TargetGroups'][0]['TargetGroupArn']
            print(f"  TG '{tg_name}' criado (porta {port}, health: {health_path})")
        except ClientError as e:
            if e.response['Error']['Code'] == 'DuplicateTargetGroupName':
                tgs = elbv2_client.describe_target_groups(Names=[tg_name])
                tg_arn = tgs['TargetGroups'][0]['TargetGroupArn']
                print(f"  TG '{tg_name}' já existe.")
            else:
                raise

        tg_arns[sim_key] = tg_arn

        # general_api é o default route
        if sim_key == "general_api":
            default_tg_arn = tg_arn

    # 3. Criar Listener (porta 80, default → general_api)
    listeners = elbv2_client.describe_listeners(LoadBalancerArn=alb_arn)['Listeners']
    listener_arn = next((l['ListenerArn'] for l in listeners if l['Port'] == 80), None)

    if not listener_arn:
        listener_resp = elbv2_client.create_listener(
            LoadBalancerArn=alb_arn,
            Protocol='HTTP',
            Port=80,
            DefaultActions=[{'Type': 'forward', 'TargetGroupArn': default_tg_arn}]
        )
        listener_arn = listener_resp['Listeners'][0]['ListenerArn']
        print(f"  Listener criado (default → general_api)")
    else:
        print(f"  Listener já existe.")

    # 4. Criar routing rules para os simuladores
    existing_rules = elbv2_client.describe_rules(ListenerArn=listener_arn)['Rules']
    existing_priorities = {r.get('Priority') for r in existing_rules}

    for sim_key, sim_config in SIMULATORS.items():
        if sim_config["TYPE"] != "service":
            continue

        priority = sim_config.get("ALB_PRIORITY")
        patterns = sim_config.get("ALB_PATH_PATTERNS")

        if not priority or not patterns:
            continue  # general_api não tem path rule (é o default)

        if str(priority) in existing_priorities:
            print(f"  Rule prioridade {priority} já existe.")
            continue

        elbv2_client.create_rule(
            ListenerArn=listener_arn,
            Conditions=[{'Field': 'path-pattern', 'Values': patterns}],
            Priority=priority,
            Actions=[{'Type': 'forward', 'TargetGroupArn': tg_arns[sim_key]}]
        )
        print(f"  Rule prioridade {priority}: {patterns} → {sim_key}")

    return alb_dns, alb_arn, tg_arns


def setup_auto_scaling(sim_key: str, service_name: str):
    """Configura o Application Auto Scaling para um ECS Service."""
    try:
        app_autoscaling = boto3.client('application-autoscaling', region_name=AWS_REGION)
        min_cap = 0 if sim_key == "sim_pedidos" else 1

        app_autoscaling.register_scalable_target(
            ServiceNamespace='ecs',
            ResourceId=f'service/{CLUSTER_NAME}/{service_name}',
            ScalableDimension='ecs:service:DesiredCount',
            MinCapacity=min_cap,
            MaxCapacity=15
        )
        
        app_autoscaling.put_scaling_policy(
            ServiceNamespace='ecs',
            ResourceId=f'service/{CLUSTER_NAME}/{service_name}',
            ScalableDimension='ecs:service:DesiredCount',
            PolicyName=f'{service_name}-cpu-autoscaling',
            PolicyType='TargetTrackingScaling',
            TargetTrackingScalingPolicyConfiguration={
                'TargetValue': 25.0,
                'PredefinedMetricSpecification': {
                    'PredefinedMetricType': 'ECSServiceAverageCPUUtilization'
                },
                'ScaleOutCooldown': 15,
                'ScaleInCooldown': 60
            }
        )
        print(f"  [{sim_key}] Auto Scaling configurado.")
    except Exception as e:
        print(f"  [{sim_key}] Aviso: Falha ao configurar Auto Scaling: {e}")


def create_ecs_service(sim_key: str, sim_config: dict, sg_id: str,
                        subnet_ids: list[str], tg_arn: str | None = None):
    """Cria um ECS Service."""
    service_name = sim_config["SERVICE_NAME"]
    task_family = sim_config["TASK_FAMILY"]
    desired = sim_config.get("DESIRED_COUNT", 1)

    print(f"  [{sim_key}] Service: {service_name} (desiredCount={desired})")

    lb_config = []
    if tg_arn and "CONTAINER_PORT" in sim_config:
        lb_config = [{
            "targetGroupArn": tg_arn,
            "containerName": sim_config["CONTAINER_NAME"],
            "containerPort": sim_config["CONTAINER_PORT"]
        }]

    try:
        response = ecs_client.describe_services(cluster=CLUSTER_NAME, services=[service_name])
        if response['services'] and response['services'][0]['status'] != 'INACTIVE':
            print(f"  [{sim_key}] Service já existe. Atualizando.")
            ecs_client.update_service(
                cluster=CLUSTER_NAME,
                service=service_name,
                taskDefinition=task_family,
                forceNewDeployment=True
            )
            setup_auto_scaling(sim_key, service_name)
            return
    except ClientError:
        pass

    ecs_client.create_service(
        cluster=CLUSTER_NAME,
        serviceName=service_name,
        taskDefinition=task_family,
        desiredCount=desired,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED"
            }
        },
        loadBalancers=lb_config,
    )
    print(f"  [{sim_key}] Service criado.")
    setup_auto_scaling(sim_key, service_name)


# =========================================================================
# DEPLOY
# =========================================================================
def check_rds_populated(api_url: str) -> bool:
    """Verifica se o RDS está populado consultando a API de Cadastro."""
    import requests
    print(f"  Verificando se o RDS está populado via {api_url}...")
    try:
        # Verifica se há entregadores (geralmente o último a ser semeado)
        resp = requests.get(f"{api_url}/cadastro/entregadores", timeout=10)
        if resp.status_code == 200:
            drivers = resp.json()
            if len(drivers) > 0:
                print(f"  RDS verificado: {len(drivers)} entregadores encontrados.")
                return True
    except Exception as e:
        print(f"  Erro ao verificar RDS: {e}")
    
    print("  RDS parece vazio ou inacessível. Rodando seed_db.py...")
    return False


def run_seed_db():
    """Executa o script de seed do banco de dados."""
    seed_script = ROOT_DIR / "database" / "seed_db.py"
    if not seed_script.exists():
        print(f"  ERRO: Script de seed não encontrado em {seed_script}")
        return False
    
    print(f"  Executando {seed_script.name}...")
    try:
        subprocess.run(["uv", "run", "python3", str(seed_script)], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  Erro ao executar seed_db.py: {e}")
        return False

def run_dynamo_setup():
    """Executa o setup_db do dynamo para criar tabela e seed de entregadores."""
    setup_script = ROOT_DIR / "dynamo" / "setup_db.py"
    if not setup_script.exists():
        print(f"  ERRO: Script de setup dynamo não encontrado em {setup_script}")
        return

    print(f"  Iniciando setup e seed do DynamoDB...")
    try:
        subprocess.run(["uv", "run", "python3", str(setup_script)], check=True)
    except Exception as e:
        print(f"  Erro no setup do Dynamo: {e}")


def print_infra_stats(api_url: str):
    """Pronta estatísticas básicas da infraestrutura populada."""
    import requests
    print("\n" + "=" * 40)
    print("ESTATÍSTICAS DA INFRAESTRUTURA")
    print("=" * 40)
    
    endpoints = {
        "Usuários": f"{api_url}/cadastro/usuarios",
        "Restaurantes": f"{api_url}/cadastro/restaurantes",
        "Entregadores (RDS)": f"{api_url}/cadastro/entregadores",
    }
    
    for label, url in endpoints.items():
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  {label:<20}: {len(data)}")
            else:
                print(f"  {label:<20}: Erro ({resp.status_code})")
        except Exception as e:
            print(f"  {label:<20}: Falha ao conectar")

   
    print("=" * 40 + "\n")


def deploy():
    """Faz o deploy completo de toda a infraestrutura de simuladores."""
    print("=" * 60)
    print("DEPLOY DO CLUSTER DE SIMULADORES DIJKFOOD")
    print("=" * 60)

    deploy_output = load_main_deploy_output()
    api_url = deploy_output.get("API_URL", "http://localhost")
    
    # --- CHECK RDS START ---
    if not check_rds_populated(api_url):
        run_seed_db()
    # --- CHECK RDS END ---

    # --- SETUP DYNAMO & SEED DRIVERS ---
    run_dynamo_setup()

    # --- PRINT STATS ---
    print_infra_stats(api_url)

    main_sg_id = deploy_output.get("SG_ID")

    vpc_id, subnet_ids = get_vpc_and_subnets()
    sg_id = setup_security_group(vpc_id, main_sg_id)
    setup_log_group()

    print(f"  Criando/Verificando cluster: {CLUSTER_NAME}")
    try:
        ecs_client.create_cluster(clusterName=CLUSTER_NAME)
    except Exception:
        pass

    account_id = sts_client.get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{account_id}:role/LabRole"

    ecr_uris = {}
    for sim_key, sim_config in SIMULATORS.items():
        ecr_uris[sim_key] = build_and_push_image(sim_key, sim_config)

    sim_alb_dns, sim_alb_arn, tg_arns = setup_internal_alb(vpc_id, subnet_ids, sg_id)
    sim_alb_url = f"http://{sim_alb_dns}"

    for sim_key, sim_config in SIMULATORS.items():
        env_mapping = sim_config.get("ENV_MAPPING", {})
        env_vars = resolve_env_vars(env_mapping, deploy_output, sim_alb_url)
        register_task_definition(sim_key, sim_config, ecr_uris[sim_key], env_vars, role_arn)

    for sim_key, sim_config in SIMULATORS.items():
        if sim_config["TYPE"] == "service":
            tg_arn = tg_arns.get(sim_key)
            create_ecs_service(sim_key, sim_config, sg_id, subnet_ids, tg_arn)

    # Output Final
    sim_output = {
        "CLUSTER_NAME": CLUSTER_NAME,
        "SG_ID": sg_id,
        "VPC_ID": vpc_id,
        "SUBNET_IDS": subnet_ids,
        "SIM_ALB_DNS": sim_alb_dns,
        "SIM_ALB_ARN": sim_alb_arn,
        "SIM_ALB_URL": sim_alb_url,
        "SIMULATORS": {}
    }
    for sim_key, sim_config in SIMULATORS.items():
        sim_output["SIMULATORS"][sim_key] = {
            "ECR_URI": ecr_uris[sim_key],
            "TASK_FAMILY": sim_config["TASK_FAMILY"],
            "CONTAINER_NAME": sim_config["CONTAINER_NAME"],
            "TYPE": sim_config["TYPE"],
            "SERVICE_NAME": sim_config.get("SERVICE_NAME"),
            "TG_ARN": tg_arns.get(sim_key),
        }

    with open(SIMULATOR_OUTPUT_PATH, "w") as f:
        json.dump(sim_output, f, indent=2)

    print("=" * 60)
    print("DEPLOY DOS SIMULADORES FINALIZADO!")
    print(f"  ALB interno: {sim_alb_url}")
    print("=" * 60)


# =========================================================================
# DESTROY
# =========================================================================
def destroy():
    """Destrói todos os recursos do cluster de simuladores."""
    print("=" * 60)
    print("DESTRUINDO INFRAESTRUTURA DE SIMULADORES")
    print("=" * 60)

    for sim_key, sim_config in SIMULATORS.items():
        if sim_config["TYPE"] == "service":
            service_name = sim_config["SERVICE_NAME"]
            try:
                ecs_client.update_service(cluster=CLUSTER_NAME, service=service_name, desiredCount=0)
                ecs_client.delete_service(cluster=CLUSTER_NAME, service=service_name, force=True)
                print(f"  Service {service_name} deletado.")
            except Exception:
                pass

    try:
        task_arns = ecs_client.list_tasks(cluster=CLUSTER_NAME)['taskArns']
        for task_arn in task_arns:
            ecs_client.stop_task(cluster=CLUSTER_NAME, task=task_arn, reason="Destroy simulators")
    except Exception:
        pass

    try:
        albs = elbv2_client.describe_load_balancers(Names=[SIM_ALB_NAME])
        alb_arn = albs['LoadBalancers'][0]['LoadBalancerArn']
        listeners = elbv2_client.describe_listeners(LoadBalancerArn=alb_arn)['Listeners']
        for listener in listeners:
            elbv2_client.delete_listener(ListenerArn=listener['ListenerArn'])
        elbv2_client.delete_load_balancer(LoadBalancerArn=alb_arn)
        print(f"  ALB {SIM_ALB_NAME} deletado.")
    except Exception:
        pass

    for sim_key, sim_config in SIMULATORS.items():
        if sim_config["TYPE"] == "service":
            tg_name = sim_config.get("TG_NAME")
            try:
                tgs = elbv2_client.describe_target_groups(Names=[tg_name])
                for tg in tgs['TargetGroups']:
                    elbv2_client.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
                    print(f"  TG {tg_name} deletado.")
            except Exception:
                pass

    try:
        ecs_client.delete_cluster(cluster=CLUSTER_NAME)
        print(f"  Cluster {CLUSTER_NAME} deletado.")
    except Exception:
        pass

    for sim_key, sim_config in SIMULATORS.items():
        repo_name = sim_config["REPO_NAME"]
        try:
            ecr_client.delete_repository(repositoryName=repo_name, force=True)
            print(f"  ECR {repo_name} deletado.")
        except Exception:
            pass

    try:
        logs_client.delete_log_group(logGroupName=LOG_GROUP_NAME)
        print(f"  Log Group {LOG_GROUP_NAME} deletado.")
    except Exception:
        pass

    try:
        ec2_client.delete_security_group(GroupName=SG_NAME)
        print(f"  SG {SG_NAME} deletado.")
    except Exception:
        pass

    if SIMULATOR_OUTPUT_PATH.exists():
        SIMULATOR_OUTPUT_PATH.unlink()

    print("=" * 60)
    print("INFRAESTRUTURA DE SIMULADORES REMOVIDA!")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy/Destroy cluster de simuladores DijkFood")
    parser.add_argument("--destroy", action="store_true", help="Destrói toda infraestrutura de simuladores")
    args = parser.parse_args()

    if args.destroy:
        destroy()
    else:
        deploy()
