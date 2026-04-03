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

if __name__ == '__main__':
    create_orders_table()
