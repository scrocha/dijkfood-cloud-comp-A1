import boto3
import subprocess
import base64
import os
import time
from pathlib import Path

# Configurações AWS
AWS_REGION = "us-east-1"
CLUSTER_NAME = "dijkfood-cluster"
TASK_FAMILY = "dijkfood-pedidos-task"
REPO_NAME = "dijkfood-api-pedidos"
SG_NAME = "dijkfood-sg-unified"
TABLE_NAME = "DijkfoodOrders"

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DOCKERFILE_PATH = BASE_DIR / "Dockerfile"

ec2_client = boto3.client("ec2", region_name=AWS_REGION)
ecr_client = boto3.client("ecr", region_name=AWS_REGION)
ecs_client = boto3.client("ecs", region_name=AWS_REGION)
sts_client = boto3.client("sts", region_name=AWS_REGION)

def setup_dynamodb():
    print(f"--- 1. Configurando DynamoDB: {TABLE_NAME} ---")
    from dynamo.setup_db import create_orders_table
    os.environ["AWS_REGION"] = AWS_REGION
    os.environ["DYNAMODB_TABLE_NAME"] = TABLE_NAME
    create_orders_table()

def setup_security_group():
    print("--- 2. Configurando Security Group ---")
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    try:
        sg_response = ec2_client.create_security_group(
            GroupName=SG_NAME, Description='Permite acesso a APIs DijkFood', VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 8000, 'ToPort': 8000, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}]
        )
        return sg_id
    except Exception as e:
        if "InvalidGroup.Duplicate" in str(e):
            return ec2_client.describe_security_groups(GroupNames=[SG_NAME])['SecurityGroups'][0]['GroupId']
        raise e

def build_and_push_docker_image():
    print("--- 3. Build e Push da Imagem Docker ---")
    account_id = sts_client.get_caller_identity()["Account"]
    ecr_uri = f"{account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com/{REPO_NAME}"
    try: ecr_client.create_repository(repositoryName=REPO_NAME)
    except: pass
    
    auth_token = ecr_client.get_authorization_token()['authorizationData'][0]
    token = base64.b64decode(auth_token['authorizationToken']).decode('utf-8').split(':')
    subprocess.run(["docker", "login", "--username", token[0], "--password-stdin", auth_token['proxyEndpoint']], input=token[1].encode('utf-8'), check=True)
    subprocess.run(["docker", "build", "-t", REPO_NAME, "-f", str(DOCKERFILE_PATH), str(ROOT_DIR)], check=True)
    subprocess.run(["docker", "tag", f"{REPO_NAME}:latest", f"{ecr_uri}:latest"], check=True)
    subprocess.run(["docker", "push", f"{ecr_uri}:latest"], check=True)
    return ecr_uri

def deploy_and_run(ecr_uri, sg_id):
    print("--- 4. Deploy no ECS Fargate ---")
    account_id = sts_client.get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{account_id}:role/LabRole"
    try: ecs_client.create_cluster(clusterName=CLUSTER_NAME)
    except: pass

    # Register Task com LabRole para evitar erros de Service-Linked Role
    response = ecs_client.register_task_definition(
        family=TASK_FAMILY, 
        networkMode="awsvpc", 
        requiresCompatibilities=["FARGATE"],
        cpu="256", memory="512", 
        executionRoleArn=role_arn, taskRoleArn=role_arn,
        containerDefinitions=[{
            "name": "order-service", 
            "image": f"{ecr_uri}:latest",
            "portMappings": [{"containerPort": 8000, "hostPort": 8000}],
            "environment": [{"name": "AWS_REGION", "value": AWS_REGION}, {"name": "DYNAMODB_TABLE_NAME", "value": TABLE_NAME}],
            "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/dijkfood-pedidos", "awslogs-region": AWS_REGION, "awslogs-stream-prefix": "ecs", "awslogs-create-group": "true"}}
        }]
    )
    task_def_arn = response['taskDefinition']['taskDefinitionArn']

    subnets = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])['Vpcs'][0]['VpcId']]}])['Subnets']
    subnet_ids = [s['SubnetId'] for s in subnets]
    
    run_response = ecs_client.run_task(
        cluster=CLUSTER_NAME, taskDefinition=task_def_arn, launchType="FARGATE",
        networkConfiguration={'awsvpcConfiguration': {'subnets': subnet_ids, 'securityGroups': [sg_id], 'assignPublicIp': 'ENABLED'}}
    )
    task_arn = run_response['tasks'][0]['taskArn']
    
    print("Aguardando IP Público...")
    waiter = ecs_client.get_waiter('tasks_running')
    waiter.wait(cluster=CLUSTER_NAME, tasks=[task_arn])
    
    eni_id = next(attr['value'] for attr in ecs_client.describe_tasks(cluster=CLUSTER_NAME, tasks=[task_arn])['tasks'][0]['attachments'][0]['details'] if attr['name'] == 'networkInterfaceId')
    public_ip = ec2_client.describe_network_interfaces(NetworkInterfaceIds=[eni_id])['NetworkInterfaces'][0]['Association']['PublicIp']
    
    return public_ip

if __name__ == "__main__":
    setup_dynamodb()
    sg_id = setup_security_group()
    ecr_uri = build_and_push_docker_image()
    public_ip = deploy_and_run(ecr_uri, sg_id)
    print(f"\nAPI Rodando: http://{public_ip}:8000/health")
