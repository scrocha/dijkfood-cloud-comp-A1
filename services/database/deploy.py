import boto3
import time
import psycopg2
import subprocess
import os

# configurações RDS
DB_INSTANCE_TYPE = "db.t3.medium" # tipo de instância
DB_ENGINE = "postgres" # engine do banco
DB_ENGINE_VERSION = "15" # versão do engine
DB_ALLOCATED_STORAGE = 20 # 20GB de armazenamento
DB_STORAGE_TYPE = "gp3" # gp3 = ssd de uso geral
DB_BACKUP_RETENTION_PERIOD = 7 # 7 dias de backup
DB_DELETE_PROTECTION = False # False = pode deletar, True = não pode deletar
DB_PUBLICLY_ACCESSIBLE = True # True = pode acessar de qualquer lugar, False = só dentro da VPC

DB_IDENTIFIER = "dijkfood-db-instance"
DB_NAME = "dijkfood"
DB_USER = "postgres"
DB_PASSWORD = "SuperSecretPassword123!" 
DB_PORT = 5432

# configurações ECS
TASK_1_FAMILY = "dijkfood-api-task" # nome da task
TASK_1_NETWORK_MODE = "awsvpc" # modo de rede
TASK_1_CPU = "1024" # 1 vCPU
TASK_1_MEMORY = "2048" # 2GB de memória
TASK_1_CONTAINER_NAME = "api-container" # nome do container
TASK_1_DB_MIN_CONN = "1" # mínimo de conexões
TASK_1_DB_MAX_CONN = "15" # máximo de conexões

# configurações ALB
ALB_NAME = "dijkfood-alb"
ALB_SCHEME = "internet-facing"
ALB_TYPE = "application"
ALB_IP_ADDRESS_TYPE = "ipv4" 

# configurações auto scaling
AS_MIN_CAPACITY = 1
AS_MAX_CAPACITY = 10
AS_TARGET_VALUE = 70.0 # limiar de CPU
AS_SCALE_OUT_COOLDOWN = 60 # tempo em segundos para escalar
AS_SCALE_IN_COOLDOWN = 60 # tempo em segundos para reduzir
    

# configurações do Target Group de Cadastro
TG_CADASTRO_NAME = "dijkfood-tg-cadastro"
    
# diretórios base para evitar erro de caminho
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # pasta /database
ROOT_DIR = os.path.dirname(BASE_DIR) # pasta raiz do projeto

AWS_REGION = "us-east-1"
API_PORT = 8000
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
    """Cria um Security Group permitindo acesso na porta 5432 e 8000 e 80"""
    
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    try:
        sg_response = ec2_client.create_security_group(
            GroupName='dijkfood-db-sg',
            Description='Permite acesso ao PostgreSQL e API para o DijkFood',
            VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        
        # Libera portas
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                # Porta do Banco
                {'IpProtocol': 'tcp', 'FromPort': DB_PORT, 'ToPort': DB_PORT, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
                # Porta da API no container
                {'IpProtocol': 'tcp', 'FromPort': API_PORT, 'ToPort': API_PORT, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
                # Porta do ALB
                {'IpProtocol': 'tcp', 'FromPort': ALB_PORT, 'ToPort': ALB_PORT, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}
            ]
        )

        print("Criação do Security Group bem sucedida.")
        return sg_id

    except Exception as e:
        if "InvalidGroup.Duplicate" in str(e):
            print("Security Group já existe. Buscando o ID...")
            sgs = ec2_client.describe_security_groups(GroupNames=['dijkfood-db-sg'])
            return sgs['SecurityGroups'][0]['GroupId']
        raise e

def create_rds_instance(sg_id):
    """Provisiona o banco PostgreSQL na AWS"""
    
    try:
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
            PubliclyAccessible=DB_PUBLICLY_ACCESSIBLE
        )
    except Exception as e:
        if "DBInstanceAlreadyExists" not in str(e):
            raise e

    waiter = rds_client.get_waiter('db_instance_available')
    waiter.wait(DBInstanceIdentifier=DB_IDENTIFIER)
    
    response = rds_client.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
    endpoint = response['DBInstances'][0]['Endpoint']['Address']

    print(f"Criação do RDS bem sucedida. Endpoint: {endpoint}")
    return endpoint

def run_ddl_only(endpoint):
    """Executa apenas a criação das estruturas (DDL)"""

    time.sleep(5) # espera o banco ficar pronto
    try:
        conn = psycopg2.connect(
            host=endpoint, database=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        ddl_path = os.path.join(BASE_DIR, 'DDL.sql')
        with open(ddl_path, 'r', encoding='utf-8') as file:
            ddl_script = file.read()

        cursor.execute(ddl_script)
        cursor.close()
        conn.close()
        print("Criação das tabelas bem sucedida.")
        
    except Exception as e:
        print(f"Erro ao interagir com o banco: {e}")

def build_and_push_docker_image():
    """Cria o repositório ECR, constrói apontando p/ base correta e envia"""
    
    account_id = sts_client.get_caller_identity()["Account"]
    ecr_uri = f"{account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com/dijkfood-api"
    
    try:
        print("Criando repositório ECR.")
        ecr_client.create_repository(repositoryName="dijkfood-api")
    except ecr_client.exceptions.RepositoryAlreadyExistsException:
        pass

    print("Autenticando Docker na AWS.")
    login_cmd = f"aws ecr get-login-password --region {AWS_REGION} | docker login --username AWS --password-stdin {account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com"
    subprocess.run(login_cmd, shell=True, check=True)
    
    print("Fazendo Build e Push da Imagem.")
    dockerfile_path = os.path.join(BASE_DIR, "Dockerfile")
    # Usa a flag -f apontando pro Dockerfile no subfolder, mantendo \raiz de contexto de Build (ROOT_DIR)
    subprocess.run(["docker", "build", "-t", "dijkfood-api", "-f", dockerfile_path, ROOT_DIR], check=True)
    subprocess.run(["docker", "tag", "dijkfood-api:latest", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "push", f"{ecr_uri}:latest"], check=True)
    
    return ecr_uri

def deploy_api_to_ecs(ecr_uri, db_endpoint, sg_id):
    """Cria Application Load Balancer, Target Groups, ECS Cluster e Service"""
    
    account_id = sts_client.get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{account_id}:role/LabRole" 
    
    print("Criando cluster ECS Fargate.")
    ecs_client.create_cluster(clusterName="dijkfood-cluster")
    
    print("Registrando Task Definition.")
    ecs_client.register_task_definition(
        family=TASK_1_FAMILY,
        networkMode=TASK_1_NETWORK_MODE,
        requiresCompatibilities=["FARGATE"],
        cpu=TASK_1_CPU, memory=TASK_1_MEMORY,
        executionRoleArn=role_arn,
        taskRoleArn=role_arn,
        containerDefinitions=[{
            "name": TASK_1_CONTAINER_NAME,
            "image": f"{ecr_uri}:latest",
            "portMappings": [{"containerPort": API_PORT, "hostPort": API_PORT}],
            "environment": [
                {"name": "DB_HOST", "value": db_endpoint},
                {"name": "DB_NAME", "value": DB_NAME},
                {"name": "DB_USER", "value": DB_USER},
                {"name": "DB_PASS", "value": DB_PASSWORD},
                {"name": "DB_MIN_CONN", "value": TASK_1_DB_MIN_CONN},
                {"name": "DB_MAX_CONN", "value": TASK_1_DB_MAX_CONN}
            ]
        }]
    )
    
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    subnets = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    subnet_ids = [s['SubnetId'] for s in subnets['Subnets'][:2]] # Pelo menos 2 pra usar ALB
    
    # Cria o Application Load Balancer
    alb_response = elbv2_client.create_load_balancer(
        Name=ALB_NAME,
        Subnets=subnet_ids,
        SecurityGroups=[sg_id],
        Scheme=ALB_SCHEME,
        Type=ALB_TYPE,
        IpAddressType=ALB_IP_ADDRESS_TYPE
    )
    alb_arn = alb_response['LoadBalancers'][0]['LoadBalancerArn']
    alb_dns = alb_response['LoadBalancers'][0]['DNSName']
    print(f"ALB Criado. DNS: {alb_dns}. Aguardando ativação.")

    # Waiter for ALB
    waiter = elbv2_client.get_waiter('load_balancer_available')
    waiter.wait(LoadBalancerArns=[alb_arn])
    
    # Cria o Target Group para o Microsserviço de Cadastro
    tg_response = elbv2_client.create_target_group(
        Name=TG_CADASTRO_NAME,
        Protocol='HTTP',
        Port=API_PORT,
        VpcId=vpc_id,
        TargetType='ip', # para o fargate tem que ser IP
        HealthCheckProtocol='HTTP',
        HealthCheckPath='/docs', # endereço do swagger seguro
        HealthCheckIntervalSeconds=30, # intervalo entre verificações
        HealthCheckTimeoutSeconds=5, # timeout da verificação
        HealthyThresholdCount=2, # verificaçoes validas para considerar saudavel
        UnhealthyThresholdCount=2 # verificaçoes invalidas para considerar não saudavel
    )
    tg_arn = tg_response['TargetGroups'][0]['TargetGroupArn']
    
    # listener para porta 80 que joga no Target Group
    elbv2_client.create_listener(
        LoadBalancerArn=alb_arn,
        Protocol='HTTP',
        Port=ALB_PORT,
        DefaultActions=[{'Type': 'forward', 'TargetGroupArn': tg_arn}]
    )
    
    print("Iniciando ECS Service atrelado ao ALB.")
    ecs_client.create_service(
        cluster="dijkfood-cluster",
        serviceName="dijkfood-api-service",
        taskDefinition=TASK_1_FAMILY,
        desiredCount=1, # quantidade de tasks
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED"
            }
        },
        loadBalancers=[{
            "targetGroupArn": tg_arn,
            "containerName": "api-container",
            "containerPort": API_PORT
        }]
    )

    print("Configurando Auto Scaling para o ECS Service.")
    
    # registra o serviço como alvo escalável
    app_autoscaling.register_scalable_target(
        ServiceNamespace='ecs',
        ResourceId='service/dijkfood-cluster/dijkfood-api-service',
        ScalableDimension='ecs:service:DesiredCount',
        MinCapacity=AS_MIN_CAPACITY,
        MaxCapacity=AS_MAX_CAPACITY
    )

    # cria política de auto scaling
    app_autoscaling.put_scaling_policy(
        PolicyName='cpu-tracking-scaling',
        ServiceNamespace='ecs',
        ResourceId='service/dijkfood-cluster/dijkfood-api-service',
        ScalableDimension='ecs:service:DesiredCount',
        PolicyType='TargetTrackingScaling',
        TargetTrackingScalingPolicyConfiguration={
            'TargetValue': AS_TARGET_VALUE, # 70% de CPU
            'PredefinedMetricSpecification': {
                'PredefinedMetricType': 'ECSServiceAverageCPUUtilization'
            },
            'ScaleOutCooldown': AS_SCALE_OUT_COOLDOWN,
            'ScaleInCooldown': AS_SCALE_IN_COOLDOWN
        }
    )
    
    print("Aguardando Target Group registrar e as tasks se curarem (Steady State).")
    waiter = ecs_client.get_waiter('services_stable')
    waiter.wait(cluster="dijkfood-cluster", services=["dijkfood-api-service"])
    
    print(f"Criação da API concluída. Acesso pelo Load Balancer: http://{alb_dns}")
    return alb_dns, alb_arn, tg_arn

def destroy_infrastructure(sg_id):
    """Destrói todos os recursos buscando-os ativamente na AWS, sem depender de ARNs salvos no escopo."""
    print("Iniciando a destruição dos recursos AWS...")
    
    # 1. Destruir o ECS Service e Cluster
    print("Buscando ECS Service para deletar...")
    try:
        ecs_client.update_service(cluster="dijkfood-cluster", serviceName="dijkfood-api-service", desiredCount=0)
        waiter = ecs_client.get_waiter('services_stable')
        waiter.wait(cluster="dijkfood-cluster", services=["dijkfood-api-service"])
        ecs_client.delete_service(cluster="dijkfood-cluster", serviceName="dijkfood-api-service")
    except ecs_client.exceptions.ServiceNotFoundException:
        pass # Serviço já não existe
    except Exception as e:
        print(f"Aviso ao deletar ECS Service: {e}")

    try:
        ecs_client.delete_cluster(cluster="dijkfood-cluster")
    except ecs_client.exceptions.ClusterNotFoundException:
        pass
    except Exception as e:
        print(f"Aviso ao deletar ECS Cluster: {e}")

    # 2. Destruir ALB e Listeners pelo nome
    print("Buscando ALB e Target Groups para deletar...")
    try:
        albs = elbv2_client.describe_load_balancers(Names=[ALB_NAME])
        for alb in albs['LoadBalancers']:
            arn = alb['LoadBalancerArn']
            elbv2_client.delete_load_balancer(LoadBalancerArn=arn)
            waiter = elbv2_client.get_waiter('load_balancers_deleted')
            waiter.wait(LoadBalancerArns=[arn])
    except elbv2_client.exceptions.LoadBalancerNotFoundException:
        pass
    except Exception as e:
        print(f"Aviso ao deletar ALB: {e}")

    # 3. Destruir Target Group
    try:
        tgs = elbv2_client.describe_target_groups(Names=[TG_CADASTRO_NAME])
        for tg in tgs['TargetGroups']:
            elbv2_client.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
    except elbv2_client.exceptions.TargetGroupNotFoundException:
        pass
    except Exception as e:
        print(f"Aviso ao deletar Target Group: {e}")

    # 4. Destruir ECR
    print("Deletando repositório ECR...")
    try:
        ecr_client.delete_repository(repositoryName="dijkfood-api", force=True)
    except ecr_client.exceptions.RepositoryNotFoundException:
        pass
    except Exception as e:
        print(f"Aviso ao deletar ECR: {e}")
        
    # 5. Destruir RDS e Security Group
    try:
        print("Deletando o banco RDS...")
        rds_client.delete_db_instance(DBInstanceIdentifier=DB_IDENTIFIER, SkipFinalSnapshot=True)
        print("Aguardando a exclusão completa do banco...")
        waiter = rds_client.get_waiter('db_instance_deleted')
        waiter.wait(DBInstanceIdentifier=DB_IDENTIFIER)
        print("Banco de dados destruído.")
        
        ec2_client.delete_security_group(GroupId=sg_id)
        print("Security Group destruído.")
    except Exception as e:
        print(f"Aviso durante a destruição do DB/SG: {e}")

def main():
    sg_id = None
    alb_arn = None
    tg_arn = None

    try:
        print("=" * 60)
        print("Iniciando deploy do DijkFood na AWS")
        print("=" * 60)
        print()

        start_time = time.time()
        print("1. Configuração de Rede (Security Group)")
        sg_id = setup_security_group()
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()

        start_time = time.time()
        print("2. Criação do Banco RDS")
        endpoint = create_rds_instance(sg_id)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()
        
        start_time = time.time()
        print("3. Criação das tabelas")
        run_ddl_only(endpoint)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()
        
        start_time = time.time()
        print("4. Compila e Puxa Docker")
        ecr_uri = build_and_push_docker_image()
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()
        
        start_time = time.time()
        print("5. Deploy da API no ECS")
        alb_dns, alb_arn, tg_arn = deploy_api_to_ecs(ecr_uri, endpoint, sg_id)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()
        
        # endereço do ALB para acessar a API
        api_url = f"http://{alb_dns}"
        env = os.environ.copy()
        env["API_URL"] = api_url
        
        start_time = time.time()
        print("6. População do RDS (executando seed_db.py)")
        seed_path = os.path.join(BASE_DIR, "seed_db.py")
        subprocess.run(["uv", "run", "python", seed_path], env=env, check=True)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()
        
        start_time = time.time()
        print("7. Simulando carga no RDS (executando simulador_cadastro.py)")
        simulador_path = os.path.join(BASE_DIR, "simulador_cadastro.py")
        subprocess.run(["uv", "run", "python", simulador_path], env=env)
        end_time = time.time()
        print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
        print()

    except Exception as e:
        print(f"\n Erro Grave no Fluxo: {e}\n")
        
    finally:
        if sg_id:
            start_time = time.time()
            print("8. Destruição da infraestrutura")
            destroy_infrastructure(sg_id)
            end_time = time.time()
            print(f"Tempo de execução: {end_time - start_time:.2f} segundos")
            print()

        print("Execução do deploy.py finalizada e todos os serviços desligados.")

if __name__ == "__main__":
    main()