import boto3
import subprocess
import base64
from pathlib import Path


AWS_REGION = "us-east-1"
CLUSTER_NAME = "dijkfood-cluster"
TASK_FAMILY = "dijkfood-rotas-task"
REPO_NAME = "dijkfood-api-rotas"
SG_NAME = "dijkfood-sg-unified"


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DOCKERFILE_PATH = BASE_DIR / "Dockerfile"


ec2_client = boto3.client("ec2", region_name=AWS_REGION)
ecr_client = boto3.client("ecr", region_name=AWS_REGION)
ecs_client = boto3.client("ecs", region_name=AWS_REGION)
sts_client = boto3.client("sts", region_name=AWS_REGION)


def setup_security_group():
    """Cria um Security Group permitindo acesso na porta 8000"""
    print("Configurando regras de rede (Security Group)")
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    try:
        sg_response = ec2_client.create_security_group(
            GroupName=SG_NAME,
            Description='Permite acesso a API de Rotas DijkFood',
            VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                'IpProtocol': 'tcp',
                'FromPort': 8001,
                'ToPort': 8001,
                'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
            }]
        )
        print(f"Security Group '{SG_NAME}' criado com sucesso.")
        return sg_id
        
    except Exception as e:
        if "InvalidGroup.Duplicate" in str(e):
            print("Security Group já existe. Recuperando o ID")
            sgs = ec2_client.describe_security_groups(GroupNames=[SG_NAME])
            return sgs['SecurityGroups'][0]['GroupId']
        raise e

def build_and_push_docker_image():
    print("Preparando a imagem Docker da API de Rotas")
    account_id = sts_client.get_caller_identity()["Account"]
    ecr_uri = f"{account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com/{REPO_NAME}"
    
    try:
        ecr_client.create_repository(repositoryName=REPO_NAME)
    except ecr_client.exceptions.RepositoryAlreadyExistsException:
        pass

    print("Autenticando Docker no ECR")
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
    subprocess.run(["docker", "build", "-t", REPO_NAME, "-f", str(DOCKERFILE_PATH), str(ROOT_DIR)], check=True)
    subprocess.run(["docker", "tag", f"{REPO_NAME}:latest", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "push", f"{ecr_uri}:latest"], check=True)
    
    return ecr_uri

def deploy_to_ecs(ecr_uri, sg_id):
    """Deploy no ECS Fargate"""
    print("Subindo a API de Rotas no ECS Fargate")
    account_id = sts_client.get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{account_id}:role/LabRole"

    ecs_client.create_cluster(clusterName=CLUSTER_NAME)

    ecs_client.register_task_definition(
        family=TASK_FAMILY,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="512",
        memory="1024",
        executionRoleArn=role_arn,
        taskRoleArn=role_arn,
        containerDefinitions=[{
            "name": "route-api-container",
            "image": f"{ecr_uri}:latest",
            "portMappings": [{"containerPort": 8001, "hostPort": 8001}],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": f"/ecs/{REPO_NAME}",
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
        cluster=CLUSTER_NAME,
        launchType="FARGATE",
        taskDefinition=TASK_FAMILY,
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
    waiter.wait(cluster=CLUSTER_NAME, tasks=[task_arn])

    task_details = ecs_client.describe_tasks(cluster=CLUSTER_NAME, tasks=[task_arn])
    eni_id = [details['value'] for details in task_details['tasks'][0]['attachments'][0]['details'] if details['name'] == 'networkInterfaceId'][0]
    eni_details = ec2_client.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
    public_ip = eni_details['NetworkInterfaces'][0]['Association']['PublicIp']

    print(f"API de Rotas Online. IP Público: http://{public_ip}:8001/rotas/health")
    return public_ip, task_arn


def destroy_infrastructure():
    """Remove todos os recursos criados para a API de Rotas"""
    print("Iniciando a destruição dos recursos da API de Rotas")
    
    try:
        tasks = ecs_client.list_tasks(cluster=CLUSTER_NAME)['taskArns']
        if tasks:
            for task in tasks:
                ecs_client.stop_task(cluster=CLUSTER_NAME, task=task)
            print("Aguardando as tasks pararem")
            waiter = ecs_client.get_waiter('tasks_stopped')
            waiter.wait(cluster=CLUSTER_NAME, tasks=tasks)
        
        ecs_client.delete_cluster(cluster=CLUSTER_NAME)
        print("Cluster ECS deletado.")
    except Exception as e:
        print(f"Aviso ao deletar ECS: {e}")

    # 2. Deletar ECR
    try:
        ecr_client.delete_repository(repositoryName=REPO_NAME, force=True)
        print("Repositório ECR deletado.")
    except Exception as e:
        print(f"Aviso ao deletar ECR: {e}")

    # 3. Security Group
    try:
        ec2_client.delete_security_group(GroupName=SG_NAME)
        print("Security Group deletado.")
    except Exception as e:
        print(f"Aviso ao deletar Security Group: {e}")
    
    print("Destruição concluída")

if __name__ == "__main__":
    # Deploy da API ECS
    sg_id = setup_security_group()
    ecr_uri = build_and_push_docker_image()
    public_ip, _ = deploy_to_ecs(ecr_uri, sg_id)
    
    print(f"Deploy finalizado com sucesso. Acesse: http://{public_ip}:8001/rotas/health")

    # destroy_infrastructure()