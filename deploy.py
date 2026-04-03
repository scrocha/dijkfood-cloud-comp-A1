import boto3
import time
import psycopg2
import subprocess
import os
import base64
from pathlib import Path

# Assumindo que este deploy.py está sendo rodado na raiz do projeto
ROOT_DIR = Path(__file__).resolve().parent 
DATABASE_DIR = ROOT_DIR / "database"
ROUTE_DIR = ROOT_DIR / "route_service"

DOCKERFILE_CADASTRO = DATABASE_DIR / "Dockerfile"
DOCKERFILE_ROTAS = ROUTE_DIR / "Dockerfile"
DDL_PATH = DATABASE_DIR / "DDL.sql"
SEED_PATH = DATABASE_DIR / "seed_db.py"
SIMULADOR_PATH = DATABASE_DIR / "simulador_cadastro.py"

# configurações RDS
DB_INSTANCE_TYPE = "db.t3.medium"
DB_ENGINE = "postgres"
DB_ENGINE_VERSION = "15"
DB_ALLOCATED_STORAGE = 20
DB_STORAGE_TYPE = "gp3"
DB_BACKUP_RETENTION_PERIOD = 7
DB_DELETE_PROTECTION = False
DB_PUBLICLY_ACCESSIBLE = True
DB_MULTI_AZ = True
DB_STORAGE_ENCRYPTED = True

DB_IDENTIFIER = "dijkfood-db-instance"
DB_NAME = "dijkfood"
DB_USER = "postgres"
DB_PASSWORD = "SuperSecretPassword123!" 
DB_PORT = 5432

# configurações ECS
CLUSTER_NAME = "dijkfood-cluster"
TASK_CADASTRO_FAMILY = "dijkfood-cadastro-task"
TASK_ROTAS_FAMILY = "dijkfood-rotas-task"
TASK_NETWORK_MODE = "awsvpc"

TASK_CADASTRO_CPU = "1024"
TASK_CADASTRO_MEMORY = "2048"
TASK_ROTAS_CPU = "512"
TASK_ROTAS_MEMORY = "1024"

# configurações ALB
ALB_NAME = "dijkfood-alb"
ALB_SCHEME = "internet-facing"
ALB_TYPE = "application"
ALB_IP_ADDRESS_TYPE = "ipv4" 

# configurações auto scaling
AS_MIN_CAPACITY = 1
AS_MAX_CAPACITY = 10
AS_TARGET_VALUE = 10.0 # coloquei 10% para testar o auto scaling
AS_SCALE_OUT_COOLDOWN = 60
AS_SCALE_IN_COOLDOWN = 60
    
# Nomes dos Target Groups
TG_CADASTRO_NAME = "dijkfood-tg-cadastro"
TG_ROTAS_NAME = "dijkfood-tg-rotas"

# Repositórios ECR
REPO_CADASTRO = "dijkfood-api-cadastro"
REPO_ROTAS = "dijkfood-api-rotas"

AWS_REGION = "us-east-1"
API_PORT_CADASTRO = 8000
API_PORT_ROTAS = 8001
ALB_PORT = 80

# clientes boto3
rds_client = boto3.client('rds', region_name=AWS_REGION)
ec2_client = boto3.client('ec2', region_name=AWS_REGION)
ecr_client = boto3.client('ecr', region_name=AWS_REGION)
ecs_client = boto3.client('ecs', region_name=AWS_REGION)
sts_client = boto3.client('sts', region_name=AWS_REGION)
elbv2_client = boto3.client('elbv2', region_name=AWS_REGION)
app_autoscaling = boto3.client('application-autoscaling', region_name=AWS_REGION)


def setup_security_group():
    """Cria um Security Group permitindo acesso às portas necessárias"""
    
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    try:
        sg_response = ec2_client.create_security_group(
            GroupName='dijkfood-sg-unified',
            Description='Permite acesso ao PostgreSQL e APIs DijkFood',
            VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {'IpProtocol': 'tcp', 'FromPort': DB_PORT, 'ToPort': DB_PORT, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
                {'IpProtocol': 'tcp', 'FromPort': API_PORT_CADASTRO, 'ToPort': API_PORT_CADASTRO, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
                {'IpProtocol': 'tcp', 'FromPort': API_PORT_ROTAS, 'ToPort': API_PORT_ROTAS, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
                {'IpProtocol': 'tcp', 'FromPort': ALB_PORT, 'ToPort': ALB_PORT, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}
            ]
        )
        print("Criação do Security Group bem sucedida.")
        return sg_id

    except Exception as e:
        if "InvalidGroup.Duplicate" in str(e):
            print("Security Group já existe. Buscando o ID...")
            sgs = ec2_client.describe_security_groups(GroupNames=['dijkfood-sg-unified'])
            return sgs['SecurityGroups'][0]['GroupId']
        raise e


def get_or_create_rds_instance(sg_id):
    """Provisiona o banco PostgreSQL, mas reutiliza se já existir"""

    try:
        response = rds_client.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
        status = response['DBInstances'][0]['DBInstanceStatus']
        if status == 'available':
            endpoint = response['DBInstances'][0]['Endpoint']['Address']
            print(f"Banco RDS já existente. Endpoint reutilizado: {endpoint}")
            return endpoint
        else:
            print(f"Banco RDS encontrado com status '{status}'. Aguardando ficar disponível.")
            
    except rds_client.exceptions.DBInstanceNotFoundFault:
        print("Banco RDS não encontrado. Iniciando criação de um novo.")
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
            VpcSecurityGroupIds=[sg_id],
            PubliclyAccessible=DB_PUBLICLY_ACCESSIBLE,
            MultiAZ=DB_MULTI_AZ,
            StorageEncrypted=DB_STORAGE_ENCRYPTED
        )

    waiter = rds_client.get_waiter('db_instance_available')
    waiter.wait(DBInstanceIdentifier=DB_IDENTIFIER)
    
    response = rds_client.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
    endpoint = response['DBInstances'][0]['Endpoint']['Address']

    print(f"Criação do banco RDS bem sucedida. Endpoint: {endpoint}")
    return endpoint


def run_ddl_only(endpoint):
    """Executa a criação das estruturas (DDL) apenas se as tabelas não existirem"""
    try:
        conn = psycopg2.connect(
            host=endpoint, database=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Lê e executa o script do caminho correto
        with open(DDL_PATH, 'r', encoding='utf-8') as file:
            ddl_script = file.read()

        cursor.execute(ddl_script)
        cursor.close()
        conn.close()
        print("Criação das tabelas bem sucedida.")
        
    except Exception as e:
        print(f"Erro ao interagir com o banco: {e}")


def build_and_push_docker_image(repo_name, dockerfile_path, context_dir):
    """Cria o repositório ECR, constrói e envia a imagem"""

    account_id = sts_client.get_caller_identity()["Account"]
    ecr_uri = f"{account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com/{repo_name}"
    
    try:
        print(f"Criando repositório ECR: {repo_name}")
        ecr_client.create_repository(repositoryName=repo_name)
    except ecr_client.exceptions.RepositoryAlreadyExistsException:
        pass

    print(f"Autenticando Docker na AWS para {repo_name}.")
    auth_token = ecr_client.get_authorization_token()
    token = auth_token['authorizationData'][0]['authorizationToken']
    username, password = base64.b64decode(token).decode('utf-8').split(':')
    registry = auth_token['authorizationData'][0]['proxyEndpoint']

    subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", registry],
        input=password.encode('utf-8'),
        check=True, stdout=subprocess.DEVNULL
    )
    
    print(f"Fazendo Build e Push da Imagem: {repo_name}")
    subprocess.run(["docker", "build", "-t", repo_name, "-f", str(dockerfile_path), str(context_dir)], check=True)
    subprocess.run(["docker", "tag", f"{repo_name}:latest", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "push", f"{ecr_uri}:latest"], check=True)

    print(f"Build e Push da Imagem {repo_name} bem sucedidos.")
    
    return ecr_uri


def setup_auto_scaling(service_name):
    """Configura o Auto Scaling para um serviço ECS específico"""

    app_autoscaling.register_scalable_target(
        ServiceNamespace='ecs',
        ResourceId=f'service/{CLUSTER_NAME}/{service_name}',
        ScalableDimension='ecs:service:DesiredCount',
        MinCapacity=AS_MIN_CAPACITY,
        MaxCapacity=AS_MAX_CAPACITY
    )

    app_autoscaling.put_scaling_policy(
        PolicyName=f'{service_name}-cpu-scaling',
        ServiceNamespace='ecs',
        ResourceId=f'service/{CLUSTER_NAME}/{service_name}',
        ScalableDimension='ecs:service:DesiredCount',
        PolicyType='TargetTrackingScaling',
        TargetTrackingScalingPolicyConfiguration={
            'TargetValue': AS_TARGET_VALUE,
            'PredefinedMetricSpecification': {
                'PredefinedMetricType': 'ECSServiceAverageCPUUtilization'
            },
            'ScaleOutCooldown': AS_SCALE_OUT_COOLDOWN,
            'ScaleInCooldown': AS_SCALE_IN_COOLDOWN
        }
    )

    print(f"Auto Scaling configurado para o serviço {service_name}.")


def deploy_api_to_ecs(ecr_uri_cadastro, ecr_uri_rotas, db_endpoint, sg_id):
    """Cria ALB, Target Groups, ECS Cluster e Services unificados"""

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
        cpu=TASK_CADASTRO_CPU, memory=TASK_CADASTRO_MEMORY,
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
        cpu=TASK_ROTAS_CPU, memory=TASK_ROTAS_MEMORY,
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
    
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    subnets = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    subnet_ids = [s['SubnetId'] for s in subnets['Subnets'][:2]]
    
    print("Criando ALB e Listeners.")
    alb_response = elbv2_client.create_load_balancer(
        Name=ALB_NAME, Subnets=subnet_ids, SecurityGroups=[sg_id],
        Scheme=ALB_SCHEME, Type=ALB_TYPE, IpAddressType=ALB_IP_ADDRESS_TYPE
    )
    alb_arn = alb_response['LoadBalancers'][0]['LoadBalancerArn']
    alb_dns = alb_response['LoadBalancers'][0]['DNSName']
    
    waiter = elbv2_client.get_waiter('load_balancer_available')
    waiter.wait(LoadBalancerArns=[alb_arn])
    
    tg_cad_resp = elbv2_client.create_target_group(
        Name=TG_CADASTRO_NAME, Protocol='HTTP', Port=API_PORT_CADASTRO, VpcId=vpc_id, TargetType='ip',
        HealthCheckProtocol='HTTP', HealthCheckPath='/docs', HealthCheckIntervalSeconds=30
    )
    tg_cad_arn = tg_cad_resp['TargetGroups'][0]['TargetGroupArn']

    tg_rotas_resp = elbv2_client.create_target_group(
        Name=TG_ROTAS_NAME, Protocol='HTTP', Port=API_PORT_ROTAS, VpcId=vpc_id, TargetType='ip',
        HealthCheckProtocol='HTTP', HealthCheckPath='/health', HealthCheckIntervalSeconds=30
    )
    tg_rotas_arn = tg_rotas_resp['TargetGroups'][0]['TargetGroupArn']
    
    listener_resp = elbv2_client.create_listener(
        LoadBalancerArn=alb_arn, Protocol='HTTP', Port=ALB_PORT,
        DefaultActions=[{'Type': 'forward', 'TargetGroupArn': tg_cad_arn}]
    )
    listener_arn = listener_resp['Listeners'][0]['ListenerArn']

    elbv2_client.create_rule(
        ListenerArn=listener_arn,
        Conditions=[{'Field': 'path-pattern', 'Values': ['/rotas*', '/route*']}],
        Priority=10,
        Actions=[{'Type': 'forward', 'TargetGroupArn': tg_rotas_arn}]
    )
    
    print("Iniciando ECS Services atrelados ao ALB.")
    ecs_client.create_service(
        cluster=CLUSTER_NAME, serviceName="dijkfood-cadastro-service", taskDefinition=TASK_CADASTRO_FAMILY,
        desiredCount=1, launchType="FARGATE",
        networkConfiguration={"awsvpcConfiguration": {"subnets": subnet_ids, "securityGroups": [sg_id], "assignPublicIp": "ENABLED"}},
        loadBalancers=[{"targetGroupArn": tg_cad_arn, "containerName": "cadastro-container", "containerPort": API_PORT_CADASTRO}]
    )

    ecs_client.create_service(
        cluster=CLUSTER_NAME, serviceName="dijkfood-rotas-service", taskDefinition=TASK_ROTAS_FAMILY,
        desiredCount=1, launchType="FARGATE",
        networkConfiguration={"awsvpcConfiguration": {"subnets": subnet_ids, "securityGroups": [sg_id], "assignPublicIp": "ENABLED"}},
        loadBalancers=[{"targetGroupArn": tg_rotas_arn, "containerName": "rotas-container", "containerPort": API_PORT_ROTAS}]
    )

    print("Configurando Auto Scaling para os Serviços.")
    setup_auto_scaling("dijkfood-cadastro-service")
    setup_auto_scaling("dijkfood-rotas-service")
    
    print("Aguardando Serviços ficarem online.")
    waiter = ecs_client.get_waiter('services_stable')
    waiter.wait(cluster=CLUSTER_NAME, services=["dijkfood-cadastro-service", "dijkfood-rotas-service"])
    
    print(f"Deploy Unificado Concluído! \nAPI Cadastro (Base): http://{alb_dns} \nAPI Rotas: http://{alb_dns}/rotas (ou /route)")
    return alb_dns


def destroy_infrastructure(sg_id):
    """Destrói os recursos ECS, ALB, ECR. Mantém o Banco de Dados e o SG intactos."""
    print("Iniciando a destruição dos recursos AWS (Mantendo Banco de Dados vivo)...")
    
    # 1. Destruir ECS Services e Cluster
    services = ["dijkfood-cadastro-service", "dijkfood-rotas-service"]
    for svc in services:
        try:
            ecs_client.update_service(cluster=CLUSTER_NAME, service=svc, desiredCount=0)
        except Exception:
            pass

    try:
        waiter = ecs_client.get_waiter('services_stable')
        waiter.wait(cluster=CLUSTER_NAME, services=services)
        for svc in services:
            ecs_client.delete_service(cluster=CLUSTER_NAME, service=svc)
        ecs_client.delete_cluster(cluster=CLUSTER_NAME)
        print("Cluster e Serviços ECS apagados.")
    except Exception as e:
        print(f"Aviso ao deletar ECS: {e}")

    # 2. Destruir ALB
    try:
        albs = elbv2_client.describe_load_balancers(Names=[ALB_NAME])
        for alb in albs['LoadBalancers']:
            arn = alb['LoadBalancerArn']
            elbv2_client.delete_load_balancer(LoadBalancerArn=arn)
            waiter = elbv2_client.get_waiter('load_balancers_deleted')
            waiter.wait(LoadBalancerArns=[arn])
            print("Load Balancer apagado.")
    except Exception as e:
        pass

    # 3. Destruir Target Groups
    try:
        tgs = elbv2_client.describe_target_groups(Names=[TG_CADASTRO_NAME, TG_ROTAS_NAME])
        for tg in tgs['TargetGroups']:
            elbv2_client.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
        print("Target Groups apagados.")
    except Exception:
        pass

    # 4. Destruir ECRs
    for repo in [REPO_CADASTRO, REPO_ROTAS]:
        try:
            ecr_client.delete_repository(repositoryName=repo, force=True)
            print(f"ECR {repo} apagado.")
        except Exception:
            pass
        
    print("Destruição dos serviços conectores finalizada! O Banco RDS e o Security Group foram MANTIDOS para economizar tempo no próximo deploy.")


def main():
    sg_id = None
    try:
        print("=" * 60)
        print("Iniciando deploy Unificado DijkFood na AWS")
        print("=" * 60)
        print()

        start_time = time.time()
        print("1. Configuração dos Security Groups")
        sg_id = setup_security_group()
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()

        start_time = time.time()
        print("2. Configuração do Banco RDS")
        endpoint = get_or_create_rds_instance(sg_id)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()

        start_time = time.time()
        print("3. Execução do DDL")
        run_ddl_only(endpoint)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()

        start_time = time.time()
        print("4. Build e Push das Imagens - API de Cadastro")
        ecr_uri_cadastro = build_and_push_docker_image(REPO_CADASTRO, DOCKERFILE_CADASTRO, ROOT_DIR)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()

        start_time = time.time()
        print("5. Build e Push das Imagens - API de Rotas")
        ecr_uri_rotas = build_and_push_docker_image(REPO_ROTAS, DOCKERFILE_ROTAS, ROOT_DIR)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()
        
        start_time = time.time()
        print("6. Deploy das APIs no ECS")
        alb_dns = deploy_api_to_ecs(ecr_uri_cadastro, ecr_uri_rotas, endpoint, sg_id)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()
        
        env = os.environ.copy()
        env["API_URL"] = f"http://{alb_dns}"
        
        start_time = time.time()
        print("6. População do RDS (executando seed_db.py)")
        subprocess.run(["uv", "run", "python", str(SEED_PATH)], env=env, check=True)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()
        
        start_time = time.time()
        print("7. Simulando carga no RDS (executando simulador_cadastro.py)")
        subprocess.run(["uv", "run", "python", str(SIMULADOR_PATH)], env=env)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()


    except Exception as e:
        print(f"\nErro Grave no Fluxo: {e}\n")
        
    finally:
        if sg_id:
            start_time = time.time()
            print("8. Destruição da infraestrutura")
            destroy_infrastructure(sg_id) 
            end_time = time.time()
            print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
            print()

        print("Execução do deploy.py finalizada.")

if __name__ == "__main__":
    main()