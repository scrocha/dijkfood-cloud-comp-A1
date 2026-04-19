import boto3
from botocore.exceptions import ClientError
import time
import os
import psycopg2
import subprocess
import json
import base64
import hashlib
from pathlib import Path

# =========================================================================
# CONFIGURAÇÃO DE CAMINHOS (BLINDADA)
# =========================================================================
ROOT_DIR = Path(__file__).resolve().parent 
DATABASE_DIR = ROOT_DIR / "database"
ROUTE_DIR = ROOT_DIR / "route_service"
PEDIDOS_DIR = ROOT_DIR / "dynamo"

DOCKERFILE_CADASTRO = DATABASE_DIR / "Dockerfile"
DOCKERFILE_ROTAS = ROUTE_DIR / "Dockerfile"
DOCKERFILE_PEDIDOS = PEDIDOS_DIR / "Dockerfile"
DDL_PATH = DATABASE_DIR / "DDL.sql"
SEED_PATH = DATABASE_DIR / "seed_db.py"
SIMULADOR_PATH = DATABASE_DIR / "simulador_cadastro.py"
OUTPUT_JSON_PATH = ROOT_DIR / "deploy_output.json"

# configurações RDS
DB_INSTANCE_TYPE = "db.t3.medium"
DB_ENGINE = "postgres"
DB_ENGINE_VERSION = "15"
DB_ALLOCATED_STORAGE = 20
DB_STORAGE_TYPE = "gp3"
DB_BACKUP_RETENTION_PERIOD = 7
DB_DELETE_PROTECTION = False
DB_PUBLICLY_ACCESSIBLE = True

DB_IDENTIFIER = "dijkfood-db-instance"
DB_NAME = "dijkfood"
DB_USER = "postgres"
DB_PASSWORD = "SuperSecretPassword123!" 
DB_PORT = 5432

# configurações DynamoDB
DYNAMODB_TABLE_NAME = "DijkfoodOrders"

# configurações ECS
CLUSTER_NAME = "dijkfood-cluster"
TASK_CADASTRO_FAMILY = "dijkfood-cadastro-task"
TASK_ROTAS_FAMILY = "dijkfood-rotas-task"
TASK_PEDIDOS_FAMILY = "dijkfood-pedidos-task"
TASK_NETWORK_MODE = "awsvpc"
TASK_CPU = "1024"
TASK_MEMORY = "2048"

# configurações ALB
ALB_NAME = "dijkfood-alb"
ALB_SCHEME = "internet-facing"
ALB_TYPE = "application"
ALB_IP_ADDRESS_TYPE = "ipv4" 

# =========================================================================
# CONFIGURAÇÕES DE AUTO SCALING
# =========================================================================
AS_MIN_CAPACITY = 2
AS_MAX_CAPACITY = 10 
AS_TARGET_VALUE_CPU = 20.0       # Threshold para serviços atrelados à CPU (Rotas/Pedidos)
AS_TARGET_VALUE_REQ = 50      # Threshold agressivo para Cadastro: 50 requisições/minuto por Task
AS_SCALE_OUT_COOLDOWN = 30       # Tempo rápido de reação para subir instâncias
AS_SCALE_IN_COOLDOWN = 60
    
# Nomes dos Target Groups
TG_CADASTRO_NAME = "dijkfood-tg-cadastro"
TG_ROTAS_NAME = "dijkfood-tg-rotas"
TG_PEDIDOS_NAME = "dijkfood-tg-pedidos"

# Repositórios ECR
REPO_CADASTRO = "dijkfood-api-cadastro"
REPO_ROTAS = "dijkfood-api-rotas"
REPO_PEDIDOS = "dijkfood-api-pedidos"

AWS_REGION = "us-east-1"
API_PORT_CADASTRO = 8000
API_PORT_ROTAS = 8001
API_PORT_PEDIDOS = 8002
ALB_PORT = 80

SG_NAME = 'dijkfood-sg-unified'

# clientes boto3
rds_client = boto3.client('rds', region_name=AWS_REGION)
ec2_client = boto3.client('ec2', region_name=AWS_REGION)
ecr_client = boto3.client('ecr', region_name=AWS_REGION)
ecs_client = boto3.client('ecs', region_name=AWS_REGION)
sts_client = boto3.client('sts', region_name=AWS_REGION)
elbv2_client = boto3.client('elbv2', region_name=AWS_REGION)
app_autoscaling = boto3.client('application-autoscaling', region_name=AWS_REGION)


def setup_security_group():
    print("Configurando Security Group.")
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    sg_id = None
    try:
        sg_response = ec2_client.create_security_group(
            GroupName=SG_NAME,
            Description='Permite acesso ao PostgreSQL e APIs DijkFood',
            VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        print(f"Security Group '{SG_NAME}' criado.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidGroup.Duplicate':
            sgs = ec2_client.describe_security_groups(GroupNames=[SG_NAME])
            sg_id = sgs['SecurityGroups'][0]['GroupId']
            print(f"Security Group '{SG_NAME}' já existe. ID: {sg_id}")
        else:
            raise e

    ports = [
        (DB_PORT, "PostgreSQL"),
        (API_PORT_CADASTRO, "API Cadastro"),
        (API_PORT_ROTAS, "API Rotas"),
        (API_PORT_PEDIDOS, "API Pedidos"),
        (ALB_PORT, "Load Balancer (HTTP)")
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
            print(f"Porta {port} ({label}) autorizada.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidPermission.Duplicate':
                pass
            else:
                print(f"Aviso ao autorizar porta {port}: {e}")

    return sg_id


def get_or_create_rds_instance(sg_id):
    print(f"Verificando instância RDS '{DB_IDENTIFIER}'.")
    try:
        response = rds_client.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
        status = response['DBInstances'][0]['DBInstanceStatus']
        endpoint = response['DBInstances'][0]['Endpoint']['Address']
        
        if status == 'available':
            print(f"Banco RDS pronto. Endpoint: {endpoint}")
            return endpoint
        else:
            print(f"Banco RDS status: '{status}'. Aguardando disponibilidade.")
            
    except ClientError as e:
        if e.response['Error']['Code'] == 'DBInstanceNotFound':
            print("Banco RDS não encontrado. Criando instância (pode levar alguns minutos).")
            rds_client.create_db_instance(
                DBInstanceIdentifier=DB_IDENTIFIER,
                AllocatedStorage=DB_ALLOCATED_STORAGE,
                DBInstanceClass=DB_INSTANCE_TYPE,
                Engine=DB_ENGINE,
                EngineVersion=DB_ENGINE_VERSION,
                MasterUsername=DB_USER,
                MasterUserPassword=DB_PASSWORD,
                BackupRetentionPeriod=DB_BACKUP_RETENTION_PERIOD,
                StorageType=DB_STORAGE_TYPE,
                DBName=DB_NAME,
                MultiAZ=True,
                VpcSecurityGroupIds=[sg_id],
                PubliclyAccessible=DB_PUBLICLY_ACCESSIBLE
            )
        else:
            raise e

    print("Aguardando RDS ficar 'available'.")
    waiter = rds_client.get_waiter('db_instance_available')
    waiter.wait(DBInstanceIdentifier=DB_IDENTIFIER)
    
    response = rds_client.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
    endpoint = response['DBInstances'][0]['Endpoint']['Address']
    print(f"RDS disponível: {endpoint}")
    return endpoint


def run_ddl_only(endpoint):
    try:
        conn = psycopg2.connect(
            host=endpoint, database=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        with open(DDL_PATH, 'r', encoding='utf-8') as file:
            ddl_script = file.read()

        cursor.execute(ddl_script)
        cursor.close()
        conn.close()
        print("Criação/Verificação das tabelas bem sucedida.")
        
    except Exception as e:
        print(f"Erro ao interagir com o banco: {e}")


def get_dir_hash(directory_path):
    """Gera um hash baseado no conteúdo da pasta para detectar alterações."""
    sha_hash = hashlib.sha256()
    for root, dirs, files in os.walk(directory_path):
        # Ignora pastas irrelevantes para o código em si
        dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', 'venv', '.venv', 'node_modules', '.aws']]
        for names in sorted(files):
            filepath = os.path.join(root, names)
            try:
                with open(filepath, 'rb') as f:
                    sha_hash.update(f.read())
            except Exception:
                pass
    return sha_hash.hexdigest()[:8]


def build_and_push_docker_image(repo_name, dockerfile_path, context_dir, ecr_client_param, sts_client_param, region):
    account_id = sts_client_param.get_caller_identity()["Account"]
    ecr_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}"
    
    try:
        print(f"Verificando repositório ECR: {repo_name}")
        ecr_client_param.create_repository(repositoryName=repo_name)
    except ClientError as e:
        if e.response['Error']['Code'] != 'RepositoryAlreadyExistsException':
            raise e

    # Lógica de Cache Inteligente (Hashing)
    service_dir = dockerfile_path.parent
    dir_hash = get_dir_hash(service_dir)
    image_tag = f"hash-{dir_hash}"

    try:
        # Tenta achar a imagem com essa exata assinatura no ECR
        ecr_client_param.describe_images(
            repositoryName=repo_name,
            imageIds=[{'imageTag': image_tag}]
        )
        print(f"[{repo_name}] Nenhuma alteração de código detectada. Pulando o Build e Push! (Reaproveitando imagem)")
        return ecr_uri
    except ClientError as e:
        if e.response['Error']['Code'] == 'ImageNotFoundException':
            pass # Se não achou, código mudou (ou é a primeira vez). Segue o jogo.
        else:
            raise e

    print(f"[{repo_name}] Alterações detectadas. Iniciando Build e Push...")
    auth_token = ecr_client_param.get_authorization_token()
    token = auth_token['authorizationData'][0]['authorizationToken']
    username, password = base64.b64decode(token).decode('utf-8').split(':')
    registry = auth_token['authorizationData'][0]['proxyEndpoint']

    subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", registry],
        input=password.encode('utf-8'),
        check=True, stdout=subprocess.DEVNULL
    )
    
    subprocess.run(["docker", "build", "-t", repo_name, "-f", str(dockerfile_path), str(context_dir)], check=True)
    
    # Taggea como latest (pro ECS rodar) e como hash (pro nosso script cachear depois)
    subprocess.run(["docker", "tag", f"{repo_name}:latest", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "tag", f"{repo_name}:latest", f"{ecr_uri}:{image_tag}"], check=True)
    
    subprocess.run(["docker", "push", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "push", f"{ecr_uri}:{image_tag}"], check=True)
    
    return ecr_uri


def setup_dynamodb():
    print(f"Configurando DynamoDB: {DYNAMODB_TABLE_NAME}")
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    try:
        table = dynamodb.create_table(
            TableName=DYNAMODB_TABLE_NAME,
            BillingMode='PAY_PER_REQUEST',
            AttributeDefinitions=[
                {'AttributeName': 'PK', 'AttributeType': 'S'},
                {'AttributeName': 'SK', 'AttributeType': 'S'},
                {'AttributeName': 'GSI1PK', 'AttributeType': 'S'},
                {'AttributeName': 'GSI1SK', 'AttributeType': 'S'},
                {'AttributeName': 'GSI2PK', 'AttributeType': 'S'},
                {'AttributeName': 'GSI2SK', 'AttributeType': 'S'},
            ],
            KeySchema=[
                {'AttributeName': 'PK', 'KeyType': 'HASH'},
                {'AttributeName': 'SK', 'KeyType': 'RANGE'}
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'UserIndex',
                    'KeySchema': [
                        {'AttributeName': 'GSI1PK', 'KeyType': 'HASH'},
                        {'AttributeName': 'GSI1SK', 'KeyType': 'RANGE'}
                    ],
                    'Projection': {'ProjectionType': 'ALL'}
                },
                {
                    'IndexName': 'StatusIndex',
                    'KeySchema': [
                        {'AttributeName': 'GSI2PK', 'KeyType': 'HASH'},
                        {'AttributeName': 'GSI2SK', 'KeyType': 'RANGE'}
                    ],
                    'Projection': {'ProjectionType': 'ALL'}
                }
            ]
        )
        print(f"Aguardando criação da tabela {DYNAMODB_TABLE_NAME}...")
        table.meta.client.get_waiter('table_exists').wait(TableName=DYNAMODB_TABLE_NAME)
        print("Tabela criada com sucesso.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print(f"Tabela {DYNAMODB_TABLE_NAME} já existe.")
        else:
            raise e

def setup_auto_scaling(service_name, policy_type='cpu', tg_arn=None, alb_arn=None):
    app_autoscaling.register_scalable_target(
        ServiceNamespace='ecs',
        ResourceId=f'service/{CLUSTER_NAME}/{service_name}',
        ScalableDimension='ecs:service:DesiredCount',
        MinCapacity=AS_MIN_CAPACITY,
        MaxCapacity=AS_MAX_CAPACITY
    )

    if policy_type == 'cpu':
        app_autoscaling.put_scaling_policy(
            PolicyName=f'{service_name}-cpu-scaling',
            ServiceNamespace='ecs',
            ResourceId=f'service/{CLUSTER_NAME}/{service_name}',
            ScalableDimension='ecs:service:DesiredCount',
            PolicyType='TargetTrackingScaling',
            TargetTrackingScalingPolicyConfiguration={
                'TargetValue': AS_TARGET_VALUE_CPU,
                'PredefinedMetricSpecification': {
                    'PredefinedMetricType': 'ECSServiceAverageCPUUtilization'
                },
                'ScaleOutCooldown': AS_SCALE_OUT_COOLDOWN,
                'ScaleInCooldown': AS_SCALE_IN_COOLDOWN
            }
        )
    elif policy_type == 'alb_request':
        alb_suffix = alb_arn.split('loadbalancer/')[-1]
        tg_suffix = tg_arn.split(':', 5)[-1]
        resource_label = f"{alb_suffix}/{tg_suffix}"

        app_autoscaling.put_scaling_policy(
            PolicyName=f'{service_name}-alb-req-scaling',
            ServiceNamespace='ecs',
            ResourceId=f'service/{CLUSTER_NAME}/{service_name}',
            ScalableDimension='ecs:service:DesiredCount',
            PolicyType='TargetTrackingScaling',
            TargetTrackingScalingPolicyConfiguration={
                'TargetValue': AS_TARGET_VALUE_REQ,
                'PredefinedMetricSpecification': {
                    'PredefinedMetricType': 'ALBRequestCountPerTarget',
                    'ResourceLabel': resource_label
                },
                'ScaleOutCooldown': AS_SCALE_OUT_COOLDOWN,
                'ScaleInCooldown': AS_SCALE_IN_COOLDOWN
            }
        )

def deploy_api_to_ecs(ecr_uri_cadastro, ecr_uri_rotas, ecr_uri_pedidos, db_endpoint, sg_id):
    account_id = sts_client.get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{account_id}:role/LabRole" 
    
    print("Criando cluster ECS Fargate.")
    try:
        ecs_client.create_cluster(clusterName=CLUSTER_NAME)
    except Exception:
        pass
    
    print("Registrando Task Definition de Cadastro.")
    ecs_client.register_task_definition(
        family=TASK_CADASTRO_FAMILY,
        networkMode=TASK_NETWORK_MODE,
        requiresCompatibilities=["FARGATE"],
        cpu="256", memory="512",
        executionRoleArn=role_arn, taskRoleArn=role_arn,
        containerDefinitions=[{
            "name": "cadastro-container",
            "image": f"{ecr_uri_cadastro}:latest",
            "portMappings": [{"containerPort": API_PORT_CADASTRO, "hostPort": API_PORT_CADASTRO}],
            "environment": [
                {"name": "DB_HOST", "value": db_endpoint},
                {"name": "DB_NAME", "value": DB_NAME},
                {"name": "DB_USER", "value": DB_USER},
                {"name": "DB_PASS", "value": DB_PASSWORD},
                {"name": "DB_MIN_CONN", "value": "1"},
                {"name": "DB_MAX_CONN", "value": "15"}
            ],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": f"/ecs/{REPO_CADASTRO}",
                    "awslogs-region": AWS_REGION,
                    "awslogs-stream-prefix": "ecs",
                    "awslogs-create-group": "true"
                }
            }
        }]
    )

    print("Registrando Task Definition de Rotas.")
    ecs_client.register_task_definition(
        family=TASK_ROTAS_FAMILY,
        networkMode=TASK_NETWORK_MODE,
        requiresCompatibilities=["FARGATE"],
        cpu=TASK_CPU, memory=TASK_MEMORY,
        executionRoleArn=role_arn, taskRoleArn=role_arn,
        containerDefinitions=[{
            "name": "rotas-container",
            "image": f"{ecr_uri_rotas}:latest",
            "portMappings": [{"containerPort": API_PORT_ROTAS, "hostPort": API_PORT_ROTAS}],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": f"/ecs/{REPO_ROTAS}",
                    "awslogs-region": AWS_REGION,
                    "awslogs-stream-prefix": "ecs",
                    "awslogs-create-group": "true"
                }
            }
        }]
    )

    print("Registrando Task Definition de Pedidos.")
    ecs_client.register_task_definition(
        family=TASK_PEDIDOS_FAMILY,
        networkMode=TASK_NETWORK_MODE,
        requiresCompatibilities=["FARGATE"],
        cpu=TASK_CPU, memory=TASK_MEMORY,
        executionRoleArn=role_arn, taskRoleArn=role_arn,
        containerDefinitions=[{
            "name": "pedidos-container",
            "image": f"{ecr_uri_pedidos}:latest",
            "portMappings": [{"containerPort": API_PORT_PEDIDOS, "hostPort": API_PORT_PEDIDOS}],
            "environment": [
                {"name": "AWS_REGION", "value": AWS_REGION},
                {"name": "DYNAMODB_TABLE_NAME", "value": DYNAMODB_TABLE_NAME},
                {"name": "ROOT_PATH", "value": "/pedidos"}
            ],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": f"/ecs/{REPO_PEDIDOS}",
                    "awslogs-region": AWS_REGION,
                    "awslogs-stream-prefix": "ecs",
                    "awslogs-create-group": "true"
                }
            }
        }]
    )
    
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    subnets = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    subnet_ids = [s['SubnetId'] for s in subnets['Subnets'][:2]]
    
    print("Criando/Verificando ALB e Listeners.")
    try:
        alb_response = elbv2_client.create_load_balancer(
            Name=ALB_NAME, Subnets=subnet_ids, SecurityGroups=[sg_id],
            Scheme=ALB_SCHEME, Type=ALB_TYPE, IpAddressType=ALB_IP_ADDRESS_TYPE
        )
        alb_arn = alb_response['LoadBalancers'][0]['LoadBalancerArn']
        alb_dns = alb_response['LoadBalancers'][0]['DNSName']
    except ClientError as e:
        if e.response['Error']['Code'] == 'DuplicateLoadBalancerName':
            albs = elbv2_client.describe_load_balancers(Names=[ALB_NAME])
            alb_arn = albs['LoadBalancers'][0]['LoadBalancerArn']
            alb_dns = albs['LoadBalancers'][0]['DNSName']
        else:
            raise e
    
    waiter = elbv2_client.get_waiter('load_balancer_available')
    waiter.wait(LoadBalancerArns=[alb_arn])
    
    # Target Group Cadastro
    try:
        tg_cad_resp = elbv2_client.create_target_group(
            Name=TG_CADASTRO_NAME, Protocol='HTTP', Port=API_PORT_CADASTRO, VpcId=vpc_id, TargetType='ip',
            HealthCheckProtocol='HTTP', HealthCheckPath='/cadastro/health', HealthCheckIntervalSeconds=30
        )
        tg_cad_arn = tg_cad_resp['TargetGroups'][0]['TargetGroupArn']
    except ClientError as e:
        if e.response['Error']['Code'] == 'DuplicateTargetGroupName':
            tgs = elbv2_client.describe_target_groups(Names=[TG_CADASTRO_NAME])
            tg_cad_arn = tgs['TargetGroups'][0]['TargetGroupArn']
        else:
            raise e

    # Target Group Rotas
    try:
        tg_rotas_resp = elbv2_client.create_target_group(
            Name=TG_ROTAS_NAME, Protocol='HTTP', Port=API_PORT_ROTAS, VpcId=vpc_id, TargetType='ip',
            HealthCheckProtocol='HTTP', HealthCheckPath='/rotas/health', HealthCheckIntervalSeconds=30
        )
        tg_rotas_arn = tg_rotas_resp['TargetGroups'][0]['TargetGroupArn']
    except ClientError as e:
        if e.response['Error']['Code'] == 'DuplicateTargetGroupName':
            tgs = elbv2_client.describe_target_groups(Names=[TG_ROTAS_NAME])
            tg_rotas_arn = tgs['TargetGroups'][0]['TargetGroupArn']
        else:
            raise e

    # Target Group Pedidos
    try:
        tg_ped_resp = elbv2_client.create_target_group(
            Name=TG_PEDIDOS_NAME, Protocol='HTTP', Port=API_PORT_PEDIDOS, VpcId=vpc_id, TargetType='ip',
            HealthCheckProtocol='HTTP', HealthCheckPath='/pedidos/health', HealthCheckIntervalSeconds=30
        )
        tg_ped_arn = tg_ped_resp['TargetGroups'][0]['TargetGroupArn']
    except ClientError as e:
        if e.response['Error']['Code'] == 'DuplicateTargetGroupName':
            tgs = elbv2_client.describe_target_groups(Names=[TG_PEDIDOS_NAME])
            tg_ped_arn = tgs['TargetGroups'][0]['TargetGroupArn']
        else:
            raise e
    
    # Listener
    listeners = elbv2_client.describe_listeners(LoadBalancerArn=alb_arn)['Listeners']
    listener_arn = next((l['ListenerArn'] for l in listeners if l['Port'] == ALB_PORT), None)

    if not listener_arn:
        listener_resp = elbv2_client.create_listener(
            LoadBalancerArn=alb_arn, Protocol='HTTP', Port=ALB_PORT,
            DefaultActions=[{'Type': 'forward', 'TargetGroupArn': tg_cad_arn}]
        )
        listener_arn = listener_resp['Listeners'][0]['ListenerArn']
    
    # Rules
    rules = elbv2_client.describe_rules(ListenerArn=listener_arn)['Rules']
    
    # Regra Rotas
    if not any(r.get('Priority') == '10' for r in rules):
        elbv2_client.create_rule(
            ListenerArn=listener_arn,
            Conditions=[{'Field': 'path-pattern', 'Values': ['/rotas*', '/route*']}],
            Priority=10,
            Actions=[{'Type': 'forward', 'TargetGroupArn': tg_rotas_arn}]
        )

    # Regra Pedidos
    if not any(r.get('Priority') == '20' for r in rules):
        elbv2_client.create_rule(
            ListenerArn=listener_arn,
            Conditions=[{'Field': 'path-pattern', 'Values': ['/pedidos*']}],
            Priority=20,
            Actions=[{'Type': 'forward', 'TargetGroupArn': tg_ped_arn}]
        )
    
    print("Iniciando/Atualizando ECS Services.")

    def create_or_update_service(service_name, task_family, tg_arn, container_name, container_port, policy_type='cpu'):
        try:
            response = ecs_client.describe_services(cluster=CLUSTER_NAME, services=[service_name])
            if response['services'] and response['services'][0]['status'] != 'INACTIVE':
                print(f"Serviço '{service_name}' já existe. Atualizando.")
                ecs_client.update_service(
                    cluster=CLUSTER_NAME,
                    service=service_name,
                    taskDefinition=task_family,
                    forceNewDeployment=True
                )
            else:
                print(f"Criando novo serviço '{service_name}'.")
                ecs_client.create_service(
                    cluster=CLUSTER_NAME, serviceName=service_name, taskDefinition=task_family,
                    desiredCount=3, launchType="FARGATE",
                    networkConfiguration={"awsvpcConfiguration": {"subnets": subnet_ids, "securityGroups": [sg_id], "assignPublicIp": "ENABLED"}},
                    loadBalancers=[{"targetGroupArn": tg_arn, "containerName": container_name, "containerPort": container_port}]
                )
                
            setup_auto_scaling(service_name, policy_type=policy_type, tg_arn=tg_arn, alb_arn=alb_arn)

        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidParameterException' and 'already exists' in e.response['Error']['Message']:
                print(f"Aviso: Serviço '{service_name}' detectado via erro de redundância. Forçando update.")
                ecs_client.update_service(
                    cluster=CLUSTER_NAME, service=service_name, 
                    taskDefinition=task_family, forceNewDeployment=True
                )
                setup_auto_scaling(service_name, policy_type=policy_type, tg_arn=tg_arn, alb_arn=alb_arn)
            else:
                raise e

    create_or_update_service("dijkfood-cadastro-service", TASK_CADASTRO_FAMILY, tg_cad_arn, "cadastro-container", API_PORT_CADASTRO, policy_type='alb_request')
    create_or_update_service("dijkfood-rotas-service", TASK_ROTAS_FAMILY, tg_rotas_arn, "rotas-container", API_PORT_ROTAS, policy_type='cpu')
    create_or_update_service("dijkfood-pedidos-service", TASK_PEDIDOS_FAMILY, tg_ped_arn, "pedidos-container", API_PORT_PEDIDOS, policy_type='cpu')
    
    print("Aguardando Serviços ficarem online.")
    waiter = ecs_client.get_waiter('services_stable')
    waiter.wait(cluster=CLUSTER_NAME, services=["dijkfood-cadastro-service", "dijkfood-rotas-service", "dijkfood-pedidos-service"])
    
    print(f"Deploy Unificado Concluído! \nAPI Cadastro: http://{alb_dns} \nAPI Rotas: http://{alb_dns}/rotas \nAPI Pedidos: http://{alb_dns}/pedidos")

    # Salva os endpoints para o Seed e para a UI
    endpoints = {
        "cadastro": f"http://{alb_dns}",
        "rotas": f"http://{alb_dns}",
        "pedidos": f"http://{alb_dns}"
    }
    
    with open(ROOT_DIR / "alb_endpoints.json", "w") as f:
        json.dump(endpoints, f, indent=2)
    
    # Também copia para a pasta public do Vite para que a UI carregue via fetch
    public_dir = ROOT_DIR / "demo-ui" / "public"
    if public_dir.exists():
        with open(public_dir / "alb_endpoints.json", "w") as f:
            json.dump(endpoints, f, indent=2)
            
    print(f"Arquivo alb_endpoints.json atualizado na raiz e em demo-ui/public/")
    return alb_dns


def main():
    sg_id = None
    try:
        print("=" * 60)
        print("Iniciando deploy Unificado DijkFood na AWS")
        print("=" * 60)
        print()

        sg_id = setup_security_group()
        
        endpoint = get_or_create_rds_instance(sg_id)
        run_ddl_only(endpoint)

        setup_dynamodb()
        
        ecr_uri_cadastro = build_and_push_docker_image(REPO_CADASTRO, DOCKERFILE_CADASTRO, ROOT_DIR, ecr_client, sts_client, AWS_REGION)
        ecr_uri_rotas = build_and_push_docker_image(REPO_ROTAS, DOCKERFILE_ROTAS, ROOT_DIR, ecr_client, sts_client, AWS_REGION)
        ecr_uri_pedidos = build_and_push_docker_image(REPO_PEDIDOS, DOCKERFILE_PEDIDOS, ROOT_DIR, ecr_client, sts_client, AWS_REGION)
        
        alb_dns = deploy_api_to_ecs(ecr_uri_cadastro, ecr_uri_rotas, ecr_uri_pedidos, endpoint, sg_id)
        
        print("\n" + "=" * 60)
        print("DEPLOY FINALIZADO COM SUCESSO")
        print("=" * 60)
        print(f"API Cadastro Health: http://{alb_dns}/cadastro/health")
        print(f"API Rotas Health:    http://{alb_dns}/rotas/health")
        print(f"API Pedidos Health:  http://{alb_dns}/pedidos/health") 
        print("=" * 60)
        
        # Busca dados de rede para salvar no output (usado pelo deploy de simuladores)
        vpcs_out = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
        vpc_id_out = vpcs_out['Vpcs'][0]['VpcId']
        subnets_out = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id_out]}])
        subnet_ids_out = [s['SubnetId'] for s in subnets_out['Subnets'][:2]]

        deploy_data = {
            "API_URL": f"http://{alb_dns}",
            "ALB_DNS": alb_dns,
            "SG_ID": sg_id,
            "VPC_ID": vpc_id_out,
            "SUBNET_IDS": subnet_ids_out
        }
        
        with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(deploy_data, f, indent=4)
            
        print(f"\nInformações de conexão salvas em: {OUTPUT_JSON_PATH.name}")
        print("=" * 60)

    except Exception as e:
        print(f"\nErro Grave no Fluxo: {e}\n")
        import traceback
        traceback.print_exc()
        
    finally:
        print("Execução do deploy.py finalizada.")

if __name__ == "__main__":
    main()