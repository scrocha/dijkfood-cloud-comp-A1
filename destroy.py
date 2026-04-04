import boto3
import time
import psycopg2
import argparse
import sys
import os
from botocore.exceptions import ClientError
from pathlib import Path

# Configurações (Mesmos nomes do deploy_copy.py)
AWS_REGION = "us-east-1"
DB_IDENTIFIER = "dijkfood-db-instance"
DYNAMODB_TABLE_NAME = "DijkfoodOrders"
CLUSTER_NAME = "dijkfood-cluster"
ALB_NAME = "dijkfood-alb"
SG_NAME = 'dijkfood-sg-unified'

# Credenciais para limpeza do RDS
DB_NAME = "dijkfood"
DB_USER = "postgres"
DB_PASSWORD = "SuperSecretPassword123!" 

# Caminhos (Ajustado para a raiz do projeto)
ROOT_DIR = Path(__file__).resolve().parent 
DDL_PATH = ROOT_DIR / "database" / "DDL.sql"

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
            resource_id = f'service/{CLUSTER_NAME}/{service}'
            autoscaling.deregister_scalable_target(
                ServiceNamespace='ecs', ResourceId=resource_id, ScalableDimension='ecs:service:DesiredCount'
            )
            print(f"Auto Scaling removido para {service}")
        except Exception: pass

        try:
            ecs.update_service(cluster=CLUSTER_NAME, service=service, desiredCount=0)
            ecs.delete_service(cluster=CLUSTER_NAME, service=service, force=True)
            print(f"Serviço {service} deletado.")
        except Exception: pass

    try:
        ecs.delete_cluster(cluster=CLUSTER_NAME)
        print(f"Cluster {CLUSTER_NAME} deletado.")
    except Exception: pass

def delete_load_balancer():
    print("--- Removendo Load Balancer e Target Groups ---")
    try:
        lbs = elbv2.describe_load_balancers(Names=[ALB_NAME])['LoadBalancers']
        for lb in lbs:
            elbv2.delete_load_balancer(LoadBalancerArn=lb['LoadBalancerArn'])
            print(f"ALB {ALB_NAME} deletado. Aguardando...")
            waiter = elbv2.get_waiter('load_balancers_deleted')
            waiter.wait(LoadBalancerArns=[lb['LoadBalancerArn']])
    except Exception: pass

    for tg_name in TG_NAMES:
        try:
            tgs = elbv2.describe_target_groups(Names=[tg_name])['TargetGroups']
            for tg in tgs:
                elbv2.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
                print(f"Target Group {tg_name} deletado.")
        except Exception: pass

def clear_rds_data():
    """Em vez de deletar a instância, apenas reseta o schema (apaga os dados)"""
    print("--- Limpando dados do RDS (Resetando Schema) ---")
    
    endpoint = os.getenv("DB_HOST") 
    
    if not endpoint:
        try:
            response = rds.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
            instance = response['DBInstances'][0]
            if instance['DBInstanceStatus'] != 'available':
                print(f"RDS não está disponível (Status: {instance['DBInstanceStatus']}). Pulando limpeza.")
                return
            endpoint = instance['Endpoint']['Address']
        except Exception:
            print("Não foi possível obter endpoint do RDS via API. Pulando limpeza de dados.")
            return

    try:
        print(f"Conectando em {endpoint} para resetar tabelas...")
        conn = psycopg2.connect(
            host=endpoint, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            connect_timeout=10
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        if DDL_PATH.exists():
            with open(DDL_PATH, 'r', encoding='utf-8') as file:
                cursor.execute(file.read())
            print("Tabelas do RDS resetadas com sucesso.")
        else:
            print(f"ERRO: Arquivo DDL não encontrado em {DDL_PATH}")
            
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Erro ao limpar dados do banco: {e}")

def delete_rds_instance():
    """Deleta a instância RDS permanentemente."""
    print("--- Deletando Instância RDS (HARD) ---")
    try:
        rds.delete_db_instance(
            DBInstanceIdentifier=DB_IDENTIFIER,
            SkipFinalSnapshot=True,
            DeleteAutomatedBackups=True
        )
        print(f"Instância {DB_IDENTIFIER} em processo de exclusão...")
    except Exception as e:
        print(f"Erro ao deletar RDS: {e}")

def delete_ecr_repos():
    print("--- Removendo Repositórios ECR (HARD) ---")
    for repo in REPO_NAMES:
        try:
            ecr.delete_repository(repositoryName=repo, force=True)
            print(f"Repositório {repo} deletado.")
        except Exception: pass

def delete_security_group():
    print("--- Removendo Security Group (HARD) ---")
    print("Aguardando liberação do SG (RDS costuma demorar)...")
    for i in range(10):
        try:
            ec2.delete_security_group(GroupName=SG_NAME)
            print(f"Security Group {SG_NAME} deletado.")
            return
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidGroup.NotFound': return
            print(f"Tentativa {i+1}/10: SG ainda em uso...")
            time.sleep(20)
        except Exception: break

def delete_dynamo():
    print("--- Removendo DynamoDB ---")
    try:
        dynamodb.delete_table(TableName=DYNAMODB_TABLE_NAME)
        print(f"Tabela {DYNAMODB_TABLE_NAME} deletada.")
    except Exception: pass

def delete_logs():
    print("--- Removendo Log Groups ---")
    for repo in REPO_NAMES:
        try:
            logs.delete_log_group(logGroupName=f"/ecs/{repo}")
            print(f"Log Group /ecs/{repo} deletado.")
        except Exception: pass

def main():
    parser = argparse.ArgumentParser(description="Script de Limpeza DijkFood")
    parser.add_argument("--hard", action="store_true", help="Deleta RDS, ECR e Security Group permanentemente")
    args = parser.parse_args()

    if args.hard:
        print("=" * 60)
        print("MODO HARD ATIVADO: TUDO SERÁ DELETADO")
        print("=" * 60)
    else:
        print("MODO SOFT: Preservando RDS, ECR e Security Group")

    delete_ecs_resources()
    delete_load_balancer()
    delete_logs()
    delete_dynamo()

    if args.hard:
        delete_rds_instance()
        delete_ecr_repos()
        delete_security_group()
    else:
        clear_rds_data()

    print("\nProcesso concluído!")

if __name__ == "__main__":
    main()
