import boto3
import sys
import os

def create_orders_table(dynamodb=None):
    region = os.getenv("AWS_REGION", "us-east-1")
    table_name = os.getenv("DYNAMODB_TABLE_NAME", "DijkfoodOrders")

    if not dynamodb:
        endpoint_url = os.getenv("DYNAMODB_ENDPOINT_URL")
        dynamodb = boto3.resource('dynamodb', region_name=region, endpoint_url=endpoint_url)

    try:
        # Criando a tabela no modo On-Demand
        table = dynamodb.create_table(
            TableName=table_name,
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
        print(f"Criando tabela {table_name}...")
        table.meta.client.get_waiter('table_exists').wait(TableName=table_name)
        print("Tabela criada com sucesso!")

    except Exception as e:
        if 'ResourceInUseException' in str(e):
            print(f"A tabela {table_name} já existe.")
        else:
            print(f"Erro ao criar tabela: {e}")
            sys.exit(1)

def seed_drivers_from_rds():
    """Lê os entregadores do RDS via API de Cadastro e popula o DynamoDB."""
    import json
    import requests
    from decimal import Decimal
    from pathlib import Path
    from datetime import datetime, timezone

    ROOT_DIR = Path(__file__).resolve().parent.parent
    DEPLOY_OUTPUT_PATH = ROOT_DIR / "deploy_output.json"
    
    region = os.getenv("AWS_REGION", "us-east-1")
    table_name = os.getenv("DYNAMODB_TABLE_NAME", "DijkfoodOrders")
    endpoint_url = os.getenv("DYNAMODB_ENDPOINT_URL")
    
    dynamodb = boto3.resource('dynamodb', region_name=region, endpoint_url=endpoint_url)
    table = dynamodb.Table(table_name)

    # 1. Obter API_URL do deploy_output.json
    api_url = None
    if DEPLOY_OUTPUT_PATH.exists():
        try:
            with open(DEPLOY_OUTPUT_PATH, "r") as f:
                output = json.load(f)
                api_url = output.get("API_URL")
        except Exception as e:
            print(f"Erro ao ler deploy_output.json: {e}")

    if not api_url:
        print("AVISO: API_URL não encontrada. Pulando seed de entregadores.")
        return

    print(f"Buscando entregadores em {api_url}/cadastro/entregadores...")
    try:
        response = requests.get(f"{api_url}/cadastro/entregadores", timeout=30)
        response.raise_for_status()
        drivers = response.json()
    except Exception as e:
        print(f"Erro ao consultar API de Cadastro: {e}")
        return

    print(f"Populando {len(drivers)} entregadores no DynamoDB como 'LIVRE'...")
    
    with table.batch_writer() as batch:
        for d in drivers:
            # Estrutura baseada no repository.py: PK=DRIVER#id, SK=METADATA
            item = {
                'PK': f"DRIVER#{d['entregador_id']}",
                'SK': "METADATA",
                'driver_id': d['entregador_id'],
                'nome': d['nome'],
                'status': "LIVRE",
                'last_lat': Decimal(str(d['endereco_latitude'])),
                'last_lng': Decimal(str(d['endereco_longitude'])),
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            batch.put_item(Item=item)

    print("Seed de entregadores concluído!")

if __name__ == '__main__':
    create_orders_table()
    # seed_drivers_from_rds()
