import boto3
import time
import psycopg2
import seed_db
import subprocess
import os

# diretórios base para evitar erro de caminho
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # pasta /database
ROOT_DIR = os.path.dirname(BASE_DIR) # pasta raiz do projeto

AWS_REGION = "us-east-1"
DB_IDENTIFIER = "dijkfood-db-instance"
DB_NAME = "dijkfood"
DB_USER = "postgres"
DB_PASSWORD = "SuperSecretPassword123!" 
DB_PORT = 5432

# clientes boto3
rds_client = boto3.client('rds', region_name=AWS_REGION)
ec2_client = boto3.client('ec2', region_name=AWS_REGION)
ecr_client = boto3.client('ecr', region_name=AWS_REGION)
ecs_client = boto3.client('ecs', region_name=AWS_REGION)
sts_client = boto3.client('sts', region_name=AWS_REGION)

def setup_security_group():
    """Cria um Security Group permitindo acesso na porta 5432 e 8000"""
    print("Configurando regras de rede (Security Group)...")
    
    # pega a VPC padrão da conta AWS
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    try:
        sg_response = ec2_client.create_security_group(
            GroupName='dijkfood-db-sg',
            Description='Permite acesso ao PostgreSQL e API para o DijkFood',
            VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        
        # libera a porta 5432 (Banco) e 8000 (API)
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    'IpProtocol': 'tcp',
                    'FromPort': DB_PORT,
                    'ToPort': DB_PORT,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                },
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 8000,
                    'ToPort': 8000,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                }
            ]
        )

        print("Security Group criado com sucesso!")
        return sg_id

    except Exception as e:
        if "InvalidGroup.Duplicate" in str(e):
            print("Security Group já existe. Buscando o ID...")
            sgs = ec2_client.describe_security_groups(GroupNames=['dijkfood-db-sg'])
            return sgs['SecurityGroups'][0]['GroupId']
        raise e

def create_rds_instance(sg_id):
    """Provisiona o banco PostgreSQL na AWS"""
    print("Iniciando a criação do RDS...")
    
    try:
        rds_client.create_db_instance(
            DBInstanceIdentifier=DB_IDENTIFIER,
            AllocatedStorage=20, # 20GB
            DBInstanceClass='db.t3.micro', # mais barata
            Engine='postgres',
            EngineVersion='15',
            MasterUsername=DB_USER,
            MasterUserPassword=DB_PASSWORD,
            DBName=DB_NAME,
            VpcSecurityGroupIds=[sg_id],
            PubliclyAccessible=True
        )
    except Exception as e:
        if "DBInstanceAlreadyExists" not in str(e):
            raise e

    # pausa o python até o banco ficar disponível
    waiter = rds_client.get_waiter('db_instance_available')
    waiter.wait(DBInstanceIdentifier=DB_IDENTIFIER)
    
    # pega o endpoint gerado pela AWS
    response = rds_client.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
    endpoint = response['DBInstances'][0]['Endpoint']['Address']

    print(f"RDS Disponível! Endpoint: {endpoint}")
    return endpoint

def run_database_scripts(endpoint):
    """Executa o DDL e o seu script de popular a base"""
    print("Criando tabelas (Executando DDL.sql)...")
    
    time.sleep(10) # algum tempo para o DNS propagar
    
    try:
        conn = psycopg2.connect(
            host=endpoint, database=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        # lê o ddl.sql apontando para a pasta database
        ddl_path = os.path.join(BASE_DIR, 'ddl.sql')
        with open(ddl_path, 'r', encoding='utf-8') as file:
            ddl_script = file.read()

        cursor.execute(ddl_script)
        
        cursor.close()
        conn.close()
        print("Tabelas criadas com sucesso!")
        
        print("Populando o banco de dados via seed_db.py...")

        # variaveis de conexão
        seed_db.DB_HOST = endpoint
        seed_db.DB_PASS = DB_PASSWORD
        
        # chama o seed_db.py
        seed_db.main()
        print("Execução do seed_db.py concluída! Base de dados populada!")
        
    except Exception as e:
        print(f"Erro ao interagir com o banco: {e}")

def build_and_push_docker_image():
    """Cria o repositório ECR, faz o build na raiz e envia a imagem"""
    print("Preparando a imagem Docker da API...")
    
    account_id = sts_client.get_caller_identity()["Account"]
    ecr_uri = f"{account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com/dijkfood-api"
    
    # cria o repositório ECR
    try:
        ecr_client.create_repository(repositoryName="dijkfood-api")
    except ecr_client.exceptions.RepositoryAlreadyExistsException:
        pass

    print("Autenticando Docker na AWS...")
    login_cmd = f"aws ecr get-login-password --region {AWS_REGION} | docker login --username AWS --password-stdin {account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com"
    subprocess.run(login_cmd, shell=True, check=True)
    
    print("Fazendo Build e Push da Imagem...")
    # o ROOT_DIR garante que ele vai achar o Dockerfile na raiz do projeto
    subprocess.run(["docker", "build", "-t", "dijkfood-api", ROOT_DIR], check=True)
    subprocess.run(["docker", "tag", "dijkfood-api:latest", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "push", f"{ecr_uri}:latest"], check=True)
    
    return ecr_uri

def deploy_api_to_ecs(ecr_uri, db_endpoint, sg_id):
    """Sobe o contêiner no ECS Fargate e retorna o IP Público"""
    print("Subindo a API no ECS Fargate...")
    
    account_id = sts_client.get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{account_id}:role/LabRole" # LabRole usado no AWS Academy
    
    # cria o cluster
    ecs_client.create_cluster(clusterName="dijkfood-cluster")
    
    # registra a configuração do container
    ecs_client.register_task_definition(
        family="dijkfood-api-task",
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="256", memory="512",
        executionRoleArn=role_arn,
        taskRoleArn=role_arn,
        containerDefinitions=[{
            "name": "api-container",
            "image": f"{ecr_uri}:latest",
            "portMappings": [{"containerPort": 8000, "hostPort": 8000}],
            "environment": [
                {"name": "DB_HOST", "value": db_endpoint},
                {"name": "DB_NAME", "value": DB_NAME},
                {"name": "DB_USER", "value": DB_USER},
                {"name": "DB_PASS", "value": DB_PASSWORD}
            ]
        }]
    )
    
    # descobre as redes padrao
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    subnets = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    subnet_id = subnets['Subnets'][0]['SubnetId']
    
    # inicia o container
    response = ecs_client.run_task(
        cluster="dijkfood-cluster",
        launchType="FARGATE",
        taskDefinition="dijkfood-api-task",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": [subnet_id],
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED"
            }
        }
    )
    
    task_arn = response['tasks'][0]['taskArn']
    
    print("Aguardando a API ficar online...")
    waiter = ecs_client.get_waiter('tasks_running')
    waiter.wait(cluster="dijkfood-cluster", tasks=[task_arn])
    
    # busca o IP Público alocado
    task_details = ecs_client.describe_tasks(cluster="dijkfood-cluster", tasks=[task_arn])
    eni_id = [details['value'] for details in task_details['tasks'][0]['attachments'][0]['details'] if details['name'] == 'networkInterfaceId'][0]
    eni_details = ec2_client.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
    public_ip = eni_details['NetworkInterfaces'][0]['Association']['PublicIp']
    
    print(f"API Online! IP Público: {public_ip}")
    return public_ip, task_arn

def destroy_infrastructure(sg_id, task_arn=None):
    """Deleta o RDS, ECS, ECR e o Security Group para evitar cobranças"""
    print("Iniciando a destruição dos recursos AWS...")
    
    if task_arn:
        print("Parando task do ECS e deletando cluster...")
        try:
            ecs_client.stop_task(cluster="dijkfood-cluster", task=task_arn)
            waiter = ecs_client.get_waiter('tasks_stopped')
            waiter.wait(cluster="dijkfood-cluster", tasks=[task_arn])
            ecs_client.delete_cluster(cluster="dijkfood-cluster")
        except Exception as e:
            print(f"Erro ao destruir ECS: {e}")

    print("Deletando repositório ECR...")
    try:
        ecr_client.delete_repository(repositoryName="dijkfood-api", force=True)
    except Exception as e:
        print(f"Erro ao deletar ECR: {e}")
        
    try:
        print("Deletando o banco RDS...")
        rds_client.delete_db_instance(
            DBInstanceIdentifier=DB_IDENTIFIER,
            SkipFinalSnapshot=True # pula o snapshot final para não gerar custos
        )
        
        print("Aguardando a exclusão completa do banco...")
        waiter = rds_client.get_waiter('db_instance_deleted') # espera o banco ser deletado
        waiter.wait(DBInstanceIdentifier=DB_IDENTIFIER)
        print("Banco de dados destruído.")
        
        # deleto também o security group
        ec2_client.delete_security_group(GroupId=sg_id)
        print("Security Group destruído.")
        
    except Exception as e:
        print(f"Erro durante a destruição: {e}")

def main():
    sg_id = None
    task_arn = None

    try:
        # 1. configura a rede
        sg_id = setup_security_group()
        
        # 2. cria o banco
        endpoint = create_rds_instance(sg_id)
        
        # 3. cria as tabelas e insere os dados
        run_database_scripts(endpoint)
        
        # 4. envia a imagem docker para a aws
        ecr_uri = build_and_push_docker_image()
        
        # 5. sobe a api no ecs
        public_ip, task_arn = deploy_api_to_ecs(ecr_uri, endpoint, sg_id)
        
        # 6. roda o simulador localmente apontando para a nuvem
        print("\nIniciando o simulador de carga contra a AWS...")
        env = os.environ.copy()
        env["API_URL"] = f"http://{public_ip}:8000"
        simulador_path = os.path.join(ROOT_DIR, "simulador_cadastro.py")
        subprocess.run(["uv", "run", "python", simulador_path], env=env)

    except Exception as e:
        print(f"\n Erro: {e}\n")
        
    finally:
        # destrói tudo 
        if sg_id:
            destroy_infrastructure(sg_id, task_arn)

        print("Execução do deploy.py finalizada.")

if __name__ == "__main__":
    main()