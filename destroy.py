import boto3
import time
from botocore.exceptions import ClientError

# Configurações (Mesmos nomes do deploy_copy.py)
AWS_REGION = "us-east-1"
DB_IDENTIFIER = "dijkfood-db-instance"
DYNAMODB_TABLE_NAME = "DijkfoodOrders"
CLUSTER_NAME = "dijkfood-cluster"
ALB_NAME = "dijkfood-alb"
SG_NAME = 'dijkfood-sg-unified'

TG_NAMES = ["dijkfood-tg-cadastro", "dijkfood-tg-rotas", "dijkfood-tg-pedidos"]
REPO_NAMES = ["dijkfood-api-cadastro", "dijkfood-api-rotas", "dijkfood-api-pedidos"]
SERVICES = ["dijkfood-cadastro-service", "dijkfood-rotas-service", "dijkfood-pedidos-service"]

# Clientes Boto3
ecs = boto3.client('ecs', region_name=AWS_REGION)
elbv2 = boto3.client('elbv2', region_name=AWS_REGION)
ec2 = boto3.client('ec2', region_name=AWS_REGION)
rds = boto3.client('rds', region_name=AWS_REGION)
ecr = boto3.client('ecr', region_name=AWS_REGION)
dynamodb = boto3.client('dynamodb', region_name=AWS_REGION)
autoscaling = boto3.client('application-autoscaling', region_name=AWS_REGION)
logs = boto3.client('logs', region_name=AWS_REGION)

def delete_ecs_resources():
    print("--- Removendo ECS ---")
    for service in SERVICES:
        try:
            # Remover Auto Scaling antes do serviço
            resource_id = f'service/{CLUSTER_NAME}/{service}'
            autoscaling.deregister_scalable_target(
                ServiceNamespace='ecs',
                ResourceId=resource_id,
                ScalableDimension='ecs:service:DesiredCount'
            )
            print(f"Auto Scaling removido para {service}")
        except Exception:
            pass

        try:
            ecs.update_service(cluster=CLUSTER_NAME, service=service, desiredCount=0)
            ecs.delete_service(cluster=CLUSTER_NAME, service=service, force=True)
            print(f"Serviço {service} deletado.")
        except Exception:
            pass

    try:
        ecs.delete_cluster(cluster=CLUSTER_NAME)
        print(f"Cluster {CLUSTER_NAME} deletado.")
    except Exception:
        pass

def delete_load_balancer():
    print("--- Removendo Load Balancer e Target Groups ---")
    try:
        lbs = elbv2.describe_load_balancers(Names=[ALB_NAME])['LoadBalancers']
        for lb in lbs:
            elbv2.delete_load_balancer(LoadBalancerArn=lb['LoadBalancerArn'])
            print(f"ALB {ALB_NAME} deletado. Aguardando...")
            waiter = elbv2.get_waiter('load_balancers_deleted')
            waiter.wait(LoadBalancerArns=[lb['LoadBalancerArn']])
    except Exception:
        pass

    for tg_name in TG_NAMES:
        try:
            tgs = elbv2.describe_target_groups(Names=[tg_name])['TargetGroups']
            for tg in tgs:
                elbv2.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
                print(f"Target Group {tg_name} deletado.")
        except Exception:
            pass

def delete_rds():
    print("--- Removendo RDS (Isso pode demorar) ---")
    try:
        rds.delete_db_instance(
            DBInstanceIdentifier=DB_IDENTIFIER,
            SkipFinalSnapshot=True,
            DeleteAutomatedBackups=True
        )
        print(f"Instância RDS {DB_IDENTIFIER} sendo deletada...")
    except ClientError as e:
        print(f"RDS: {e.response['Error']['Message']}")
    except Exception:
        pass

def delete_dynamo():
    print("--- Removendo DynamoDB ---")
    try:
        dynamodb.delete_table(TableName=DYNAMODB_TABLE_NAME)
        print(f"Tabela {DYNAMODB_TABLE_NAME} deletada.")
    except Exception:
        pass

def delete_ecr_repos():
    print("--- Removendo Repositórios ECR ---")
    for repo in REPO_NAMES:
        try:
            ecr.delete_repository(repositoryName=repo, force=True)
            print(f"Repositório ECR {repo} deletado.")
        except Exception:
            pass

def delete_logs():
    print("--- Removendo Log Groups ---")
    for repo in REPO_NAMES:
        try:
            logs.delete_log_group(logGroupName=f"/ecs/{repo}")
            print(f"Log Group /ecs/{repo} deletado.")
        except Exception:
            pass

def delete_security_group():
    print("--- Removendo Security Group ---")
    print("Aguardando recursos liberarem o SG...")
    time.sleep(10) # Delay básico para ajudar na liberação
    for _ in range(5): # Tenta algumas vezes pois o RDS demora a soltar o SG
        try:
            # Check if group exists before trying to delete
            ec2.describe_security_groups(GroupNames=[SG_NAME])
            ec2.delete_security_group(GroupName=SG_NAME)
            print(f"Security Group {SG_NAME} deletado.")
            break
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidGroup.NotFound':
                print(f"Security Group {SG_NAME} não encontrado.")
                break
            print("SG ainda em uso, tentando novamente em 20s...")
            time.sleep(20)
        except Exception:
            break

def main():
    print("Iniciando DESTRUIÇÃO TOTAL da infraestrutura DijkFood...")
    delete_ecs_resources()
    delete_load_balancer()
    delete_ecr_repos()
    delete_logs()
    delete_dynamo()
    delete_rds()
    delete_security_group()
    print("\nProcesso de destruição finalizado.")
    print("Nota: O RDS e o Security Group podem demorar alguns minutos para sumir completamente do console.")

if __name__ == "__main__":
    main()
