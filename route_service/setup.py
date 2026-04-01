import osmnx as ox
import boto3
import subprocess
import base64
from pathlib import Path

PLACE_NAME = "Sao Paulo, SP, Brazil"
GRAPH_FILE_NAME = "grafo_sp.graphml"

AWS_BUCKET_NAME = "grafo-dijkfood-sp-1"
AWS_REGION = "us-east-1"

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DOCKERFILE_PATH = BASE_DIR / "Dockerfile"
GRAPH_PATH = BASE_DIR / GRAPH_FILE_NAME

s3_client = boto3.client("s3", region_name=AWS_REGION)
ec2_client = boto3.client("ec2", region_name=AWS_REGION)
ecr_client = boto3.client("ecr", region_name=AWS_REGION)
ecs_client = boto3.client("ecs", region_name=AWS_REGION)
sts_client = boto3.client("sts", region_name=AWS_REGION)

def get_s3_client():
    return s3_client

def criar_bucket(s3, bucket_name):
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"Bucket '{bucket_name}' já existe")
        return True
    except:
        try:
            print(f"Criando bucket '{bucket_name}'")
            if AWS_REGION == "us-east-1":
                s3.create_bucket(Bucket=bucket_name)
            else:
                s3.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": AWS_REGION},
                )
            print("Bucket criado com sucesso")
            return True
        except Exception as e:
            print(f"Erro ao criar bucket: {e}")
            raise

def upload_para_s3(s3, caminho_local, bucket_name, nome_s3):
    print(f"Enviando '{caminho_local}' para s3://{bucket_name}/{nome_s3}")
    s3.upload_file(str(caminho_local), bucket_name, nome_s3)
    print("Upload concluído")

def setup_security_group():
    """Cria um Security Group permitindo acesso na porta 8000"""
    print("Configurando regras de rede (Security Group)")
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    try:
        sg_name = 'dijkfood-route-sg'
        sg_response = ec2_client.create_security_group(
            GroupName=sg_name,
            Description='Permite acesso a API de Rotas DijkFood',
            VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 8000,
                    'ToPort': 8000,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                }
            ]
        )
        print("Security Group criado com sucesso")
        return sg_id
    except Exception as e:
        if "InvalidGroup.Duplicate" in str(e):
            print("Security Group já existe. Buscando o ID")
            sgs = ec2_client.describe_security_groups(GroupNames=['dijkfood-route-sg'])
            return sgs['SecurityGroups'][0]['GroupId']
        raise e

def build_and_push_docker_image():
    print("Preparando a imagem Docker da API de Rotas")
    account_id = sts_client.get_caller_identity()["Account"]
    ecr_uri = f"{account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com/dijkfood-route-api"
    
    try:
        ecr_client.create_repository(repositoryName="dijkfood-route-api")
    except ecr_client.exceptions.RepositoryAlreadyExistsException:
        pass

    print("Autenticando Docker no ECR via Boto3")
    auth_token = ecr_client.get_authorization_token()
    token = auth_token['authorizationData'][0]['authorizationToken']
    username, password = base64.b64decode(token).decode('utf-8').split(':')
    registry = auth_token['authorizationData'][0]['proxyEndpoint']

    login_proc = subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", registry],
        input=password.encode('utf-8'),
        check=True
    )
    
    print("Fazendo Build e Push da Imagem de Rotas")
    subprocess.run(["docker", "build", "-t", "dijkfood-route-api", "-f", str(DOCKERFILE_PATH), str(ROOT_DIR)], check=True)
    subprocess.run(["docker", "tag", "dijkfood-route-api:latest", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "push", f"{ecr_uri}:latest"], check=True)
    
    return ecr_uri

def deploy_to_ecs(ecr_uri, sg_id):
    """Deploy no ECS Fargate"""
    print("Subindo a API de Rotas no ECS Fargate")
    account_id = sts_client.get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{account_id}:role/LabRole"

    ecs_client.create_cluster(clusterName="dijkfood-route-cluster")

    ecs_client.register_task_definition(
        family="dijkfood-route-task",
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="512", memory="1024", 
        executionRoleArn=role_arn,
        taskRoleArn=role_arn,
        containerDefinitions=[{
            "name": "route-api-container",
            "image": f"{ecr_uri}:latest",
            "portMappings": [{"containerPort": 8000, "hostPort": 8000}],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": "/ecs/dijkfood-route-api",
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
    subnet_id = subnets['Subnets'][0]['SubnetId']

    response = ecs_client.run_task(
        cluster="dijkfood-route-cluster",
        launchType="FARGATE",
        taskDefinition="dijkfood-route-task",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": [subnet_id],
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED"
            }
        }
    )

    task_arn = response['tasks'][0]['taskArn']
    print("Aguardando a API de Rotas ficar online")
    waiter = ecs_client.get_waiter('tasks_running')
    waiter.wait(cluster="dijkfood-route-cluster", tasks=[task_arn])

    task_details = ecs_client.describe_tasks(cluster="dijkfood-route-cluster", tasks=[task_arn])
    eni_id = [details['value'] for details in task_details['tasks'][0]['attachments'][0]['details'] if details['name'] == 'networkInterfaceId'][0]
    eni_details = ec2_client.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
    public_ip = eni_details['NetworkInterfaces'][0]['Association']['PublicIp']

    print(f"API de Rotas Online. IP Público: http://{public_ip}:8000")
    return public_ip, task_arn

def baixar_grafo():
    G = ox.graph_from_place(
        PLACE_NAME,
        network_type="drive",
        simplify=True
    )
    print("Grafo baixado")
    return G

def carregar_grafo(nome_arquivo=GRAPH_FILE_NAME):
    G = ox.load_graphml(BASE_DIR / nome_arquivo)
    return G

def salvar_grafo(G, nome_arquivo=GRAPH_FILE_NAME):
    ox.save_graphml(G, BASE_DIR / nome_arquivo)

def destroy_infrastructure():
    """Remove todos os recursos criados para a API de Rotas"""
    print("Iniciando a destruição dos recursos da API de Rotas")
    
    try:
        tasks = ecs_client.list_tasks(cluster="dijkfood-route-cluster")['taskArns']
        if tasks:
            for task in tasks:
                ecs_client.stop_task(cluster="dijkfood-route-cluster", task=task)
            print("Aguardando as tasks pararem")
            waiter = ecs_client.get_waiter('tasks_stopped')
            waiter.wait(cluster="dijkfood-route-cluster", tasks=tasks)
        
        ecs_client.delete_cluster(cluster="dijkfood-route-cluster")
        print("Cluster ECS deletado.")
    except Exception as e:
        print(f"Aviso ao deletar ECS: {e}")

    # 2. Deletar ECR
    try:
        ecr_client.delete_repository(repositoryName="dijkfood-route-api", force=True)
        print("Repositório ECR deletado.")
    except Exception as e:
        print(f"Aviso ao deletar ECR: {e}")

    # 3. Security Group
    try:
        ec2_client.delete_security_group(GroupName='dijkfood-route-sg')
        print("Security Group deletado.")
    except Exception as e:
        print(f"Aviso ao deletar Security Group: {e}")
    
    print("Destruição concluída")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "destroy":
        destroy_infrastructure()
    else:
        s3 = get_s3_client()
        
        criar_bucket(s3, AWS_BUCKET_NAME)
    
        arquivo_existe_no_s3 = False
        try:
            s3.head_object(Bucket=AWS_BUCKET_NAME, Key=GRAPH_FILE_NAME)
            arquivo_existe_no_s3 = True
            print(f"Arquivo '{GRAPH_FILE_NAME}' já encontrado no S3. Pulando download e upload.")
        except:
            print(f"Arquivo '{GRAPH_FILE_NAME}' não encontrado no S3.")

        if not arquivo_existe_no_s3:
            if not GRAPH_PATH.exists():
                G = baixar_grafo()
                salvar_grafo(G, GRAPH_FILE_NAME)
            
            upload_para_s3(s3, GRAPH_PATH, AWS_BUCKET_NAME, GRAPH_FILE_NAME)

        # 4. Deploy da API ECS
        sg_id = setup_security_group()
        ecr_uri = build_and_push_docker_image()
        public_ip, _ = deploy_to_ecs(ecr_uri, sg_id)
        
        print(f"Deploy finalizado com sucesso. Acesse: http://{public_ip}:8000/health")

        # destroy_infrastructure()