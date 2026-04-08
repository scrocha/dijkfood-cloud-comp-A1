"""
Limpa apenas dados (Postgres via DDL + itens na tabela DynamoDB).
Não remove ECS, ALB, ECR, RDS nem a tabela Dynamo — a infra segue funcional.

Uso:
    uv run python clear_data_only.py
"""

import os
import sys

import boto3
from botocore.exceptions import ClientError

from destroy import clear_rds_data

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def empty_dynamo_orders_table(table_name: str, region: str) -> None:
    print("--- Esvaziando tabela DynamoDB (mantendo tabela e índices) ---")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    try:
        dynamodb.meta.client.describe_table(TableName=table_name)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            print(f"Tabela '{table_name}' não existe. Pulando DynamoDB.")
            return
        raise

    table = dynamodb.Table(table_name)
    deleted = 0
    scan_kwargs: dict = {}
    while True:
        response = table.scan(**scan_kwargs)
        items = response.get("Items", [])
        if items:
            with table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
            deleted += len(items)
        lek = response.get("LastEvaluatedKey")
        if not lek:
            break
        scan_kwargs["ExclusiveStartKey"] = lek

    print(f"DynamoDB: {deleted} itens removidos de '{table_name}'.")


def main() -> None:
    table_name = os.getenv("DYNAMODB_TABLE_NAME", "DijkfoodOrders")
    clear_rds_data()
    empty_dynamo_orders_table(table_name, AWS_REGION)
    print("\nLimpeza de dados concluída (ECS/ALB intactos).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido.", file=sys.stderr)
        sys.exit(130)
