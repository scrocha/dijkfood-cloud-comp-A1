import boto3

AWS_REGION = "us-east-1"
CLUSTER_NAME = "dijkfood-cluster"
TASK_FAMILY = "dijkfood-pedidos-task"
REPO_NAME = "dijkfood-api-pedidos"
SG_NAME = "dijkfood-sg-unified"
TABLE_NAME = "DijkfoodOrders"

ecs = boto3.client("ecs", region_name=AWS_REGION)
ecr = boto3.client("ecr", region_name=AWS_REGION)
ec2 = boto3.client("ec2", region_name=AWS_REGION)
dynamo = boto3.resource("dynamodb", region_name=AWS_REGION)

def cleanup():
    print("Iniciando limpeza...")
    
    # 1. Stop Tasks
    try:
        tasks = ecs.list_tasks(cluster=CLUSTER_NAME, family=TASK_FAMILY)['taskArns']
        for task in tasks:
            ecs.stop_task(cluster=CLUSTER_NAME, task=task)
        print("Tasks paradas.")
    except: pass

    # 2. Delete ECR
    try:
        ecr.delete_repository(repositoryName=REPO_NAME, force=True)
        print("ECR deletado.")
    except: pass

    # 3. Delete DynamoDB
    try:
        table = dynamo.Table(TABLE_NAME)
        table.delete()
        print("DynamoDB deletado.")
    except: pass

if __name__ == "__main__":
    cleanup()
