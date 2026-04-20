import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import httpx
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .models import (
    ORDER_FLOW,
    BatchDriverLocationUpdate,
    DriverStatus,
    OrderCreate,
    OrderStatus,
)


class OrderRepository:
    def __init__(self):
        region = os.getenv("AWS_REGION", "us-east-1")
        endpoint_url = os.getenv("DYNAMODB_ENDPOINT_URL")
        self.table_name = os.getenv("DYNAMODB_TABLE_NAME", "DijkfoodOrders")
        
        self.dynamodb = boto3.resource('dynamodb', region_name=region, endpoint_url=endpoint_url)
        self.table = self.dynamodb.Table(self.table_name)
        self.client = boto3.client('dynamodb', region_name=region, endpoint_url=endpoint_url)

        if os.getenv("DYNAMODB_AUTO_CREATE", "0").lower() in {"1", "true", "yes", "y"}:
            self._ensure_table_exists()

    def _ensure_table_exists(self) -> None:
        try:
            self.client.describe_table(TableName=self.table_name)
            return
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code not in {"ResourceNotFoundException"}:
                raise

        from dynamo.setup_db import create_orders_table

        create_orders_table(dynamodb=self.dynamodb)

    def create_order(self, order_data: OrderCreate):
        order_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        
        metadata_item = {
            'PK': f'ORDER#{order_id}',
            'SK': 'METADATA',
            'order_id': order_id,
            'customer_id': order_data.customer_id,
            'restaurant_id': order_data.restaurant_id,
            'status': OrderStatus.CONFIRMED.value,
            'items': order_data.items,
            'total_value': str(order_data.total_value),
            'created_at': now,
            'updated_at': now,
            'GSI1PK': f'CUSTOMER#{order_data.customer_id}',
            'GSI1SK': now,
            'GSI2PK': f'STATUS#{OrderStatus.CONFIRMED.value}',
            'GSI2SK': order_id
        }
        
        history_item = {
            'PK': f'ORDER#{order_id}',
            'SK': f'HISTORY#{OrderStatus.CONFIRMED.value}',
            'status': OrderStatus.CONFIRMED.value,
            'timestamp': now
        }

        self.client.transact_write_items(
            TransactItems=[
                {'Put': {'Item': self._to_dynamo_dict(metadata_item), 'TableName': self.table_name}},
                {'Put': {'Item': self._to_dynamo_dict(history_item), 'TableName': self.table_name}}
            ]
        )
        return metadata_item

    def update_status(self, order_id: str, next_status: OrderStatus, entregador_id: str = None):
        now = datetime.now(timezone.utc).isoformat()
        order = self.get_order(order_id)
        if not order:
            raise ValueError("Pedido não encontrado")
        
        current_status = OrderStatus(order['status'])
        if next_status not in ORDER_FLOW.get(current_status, []):
            raise ValueError(f"Transição de {current_status} para {next_status} não é permitida.")

        # Monta a expressão de atualização para os metadados
        update_expr = "SET #s = :new_status, updated_at = :now, GSI2PK = :new_gsi2pk"
        expr_attr_values = {
            ':new_status': next_status.value,
            ':current_status': current_status.value,
            ':now': now,
            ':new_gsi2pk': f'STATUS#{next_status.value}'
        }
        expr_attr_names = {'#s': 'status'}

        # Se enviou entregador_id, salva nos metadados (importante para o SQL)
        final_entregador_id = entregador_id or order.get('entregador_id')
        if final_entregador_id:
            update_expr += ", entregador_id = :entregador_id"
            expr_attr_values[':entregador_id'] = final_entregador_id

        transact_items = [
            {
                'Update': {
                    'TableName': self.table_name,
                    'Key': {'PK': {'S': f'ORDER#{order_id}'}, 'SK': {'S': 'METADATA'}},
                    'UpdateExpression': update_expr,
                    'ConditionExpression': "#s = :current_status",
                    'ExpressionAttributeNames': expr_attr_names,
                    'ExpressionAttributeValues': self._to_dynamo_dict(expr_attr_values)
                }
            },
            {
                'Put': {
                    'TableName': self.table_name,
                    'Item': self._to_dynamo_dict({
                        'PK': f'ORDER#{order_id}',
                        'SK': f'HISTORY#{next_status.value}',
                        'status': next_status.value,
                        'timestamp': now
                    })
                }
            }
        ]

        # Lógica de transição de status do entregador
        if next_status == OrderStatus.PREPARING:
            if not final_entregador_id:
                raise ValueError("entregador_id é obrigatório ao transicionar para PREPARING, pois ele acabou de ser associado.")
            
            # Garante que o entregador está LIVRE antes de assumir o pedido
            transact_items.append({
                'Update': {
                    'TableName': self.table_name,
                    'Key': {'PK': {'S': f'DRIVER#{final_entregador_id}'}, 'SK': {'S': 'LATEST'}},
                    'UpdateExpression': "SET #s = :busy, GSI2PK = :busy_gsi, order_id = :order_id",
                    'ConditionExpression': "#s = :free OR attribute_not_exists(#s)",
                    'ExpressionAttributeNames': {'#s': 'status'},
                    'ExpressionAttributeValues': self._to_dynamo_dict({
                        ':busy': DriverStatus.EM_ENTREGA.value,
                        ':busy_gsi': f'DRIVER_STATUS#{DriverStatus.EM_ENTREGA.value}',
                        ':free': DriverStatus.LIVRE.value,
                        ':order_id': order_id
                    })
                }
            })
        
        elif next_status == OrderStatus.DELIVERED:
            if final_entregador_id:
                # Libera o entregador
                transact_items.append({
                    'Update': {
                        'TableName': self.table_name,
                        'Key': {'PK': {'S': f'DRIVER#{final_entregador_id}'}, 'SK': {'S': 'LATEST'}},
                        'UpdateExpression': "SET #s = :free, GSI2PK = :free_gsi REMOVE order_id",
                        'ExpressionAttributeNames': {'#s': 'status'},
                        'ExpressionAttributeValues': self._to_dynamo_dict({
                            ':free': DriverStatus.LIVRE.value,
                            ':free_gsi': f'DRIVER_STATUS#{DriverStatus.LIVRE.value}'
                        })
                    }
                })

        try:
            self.client.transact_write_items(TransactItems=transact_items)
        except self.client.exceptions.TransactionCanceledException as e:
            reasons = e.response.get('CancellationReasons', [])
            if any(r.get('Code') == 'ConditionalCheckFailed' for r in reasons):
                # Identifica se foi o status do pedido ou do entregador
                if reasons[0].get('Code') == 'ConditionalCheckFailed':
                    raise ValueError(f"Pedido {order_id} já mudou de status ou não está mais em {current_status}.")
                if len(reasons) > 2 and reasons[2].get('Code') == 'ConditionalCheckFailed':
                    raise ValueError(f"Entregador {final_entregador_id} não está disponível (LIVRE).")
            raise e

        # Se o pedido foi entregue, condensa e sincroniza com o PostgreSQL
        if next_status == OrderStatus.DELIVERED:
            self._sync_to_postgres(order_id)

        return True

    def _sync_to_postgres(self, order_id: str):
        """Condensa informações do DynamoDB e envia para a tabela SQL via API de cadastro."""
        try:
            metadata = self.get_order(order_id)
            history = self.get_order_history(order_id)
            
            # Condensa os timestamps de cada status
            # SQL espera: CONFIRMED_TIME, PREPARING_TIME, READY_FOR_PICKUP_TIME, PICKED_UP_TIME, IN_TRANSIT_TIME, DELIVERED_TIME
            status_times = {h['status']: h['timestamp'] for h in history}
            
            # Se faltar algum status intermediário (ex: pulou etapa), 
            # usamos o updated_at ou o tempo atual como fallback seguro
            fallback = metadata.get('updated_at')

            payload = {
                "pedido_id": order_id,
                "user_id": metadata.get('customer_id'),
                "rest_id": metadata.get('restaurant_id'),
                "entregador_id": metadata.get('entregador_id', 'SEM_ENTREGADOR'),
                "confirmed_time": status_times.get('CONFIRMED', fallback),
                "preparing_time": status_times.get('PREPARING', fallback),
                "ready_for_pickup_time": status_times.get('READY_FOR_PICKUP', fallback),
                "picked_up_time": status_times.get('PICKED_UP', fallback),
                "in_transit_time": status_times.get('IN_TRANSIT', fallback),
                "delivered_time": status_times.get('DELIVERED', fallback)
            }

            # URL do serviço de cadastro (PostgreSQL)
            # - Local (docker-compose): http://database-service:8000
            # - AWS (ALB): http://<alb-dns>
            cadastro_base = os.getenv("CADASTRO_SERVICE_URL", "http://database-service:8000").rstrip("/")
            db_url = f"{cadastro_base}/cadastro/pedidos"
            
            with httpx.Client() as client:
                resp = client.post(db_url, json=payload, timeout=5.0)
                resp.raise_for_status()
                print(f"Pedido {order_id} sincronizado com PostgreSQL com sucesso.")
        except Exception as e:
            print(f"Erro ao sincronizar pedido {order_id} com PostgreSQL: {e}")

    def get_order(self, order_id: str):
        response = self.table.get_item(Key={'PK': f'ORDER#{order_id}', 'SK': 'METADATA'})
        return response.get('Item')

    def get_order_history(self, order_id: str):
        response = self.table.query(
            KeyConditionExpression=Key('PK').eq(f'ORDER#{order_id}') & Key('SK').begins_with('HISTORY#')
        )
        return response.get('Items', [])

    def list_by_customer(self, customer_id: str):
        response = self.table.query(
            IndexName='UserIndex', 
            KeyConditionExpression=Key('GSI1PK').eq(f'CUSTOMER#{customer_id}')
        )
        return response.get('Items', [])

    def list_by_status(self, status: str):
        response = self.table.query(
            IndexName='StatusIndex', 
            KeyConditionExpression=Key('GSI2PK').eq(f'STATUS#{status}')
        )
        return response.get('Items', [])

    def _convert_floats(self, obj):
        """Converte recursivamente floats para Decimal, pois o boto3/dynamodb não aceita float."""
        if isinstance(obj, list):
            return [self._convert_floats(i) for i in obj]
        elif isinstance(obj, dict):
            return {k: self._convert_floats(v) for k, v in obj.items()}
        elif isinstance(obj, float):
            return Decimal(str(obj))
        return obj

    def _to_dynamo_dict(self, python_dict):
        from boto3.dynamodb.types import TypeSerializer
        serializer = TypeSerializer()
        # Converte floats para Decimal antes de serializar para o formato do Dynamo
        clean_dict = self._convert_floats(python_dict)
        return {k: serializer.serialize(v) for k, v in clean_dict.items()}

class LocationRepository:
    def __init__(self):
        region = os.getenv("AWS_REGION", "us-east-1")
        endpoint_url = os.getenv("DYNAMODB_ENDPOINT_URL")
        self.table_name = os.getenv("DYNAMODB_TABLE_NAME", "DijkfoodOrders")
        self.dynamodb = boto3.resource('dynamodb', region_name=region, endpoint_url=endpoint_url)
        self.table = self.dynamodb.Table(self.table_name)

        if os.getenv("DYNAMODB_AUTO_CREATE", "0").lower() in {"1", "true", "yes", "y"}:
            self._ensure_table_exists()

    def _ensure_table_exists(self) -> None:
        client = boto3.client(
            "dynamodb",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            endpoint_url=os.getenv("DYNAMODB_ENDPOINT_URL"),
        )
        try:
            client.describe_table(TableName=self.table_name)
            return
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code not in {"ResourceNotFoundException"}:
                raise

        from dynamo.setup_db import create_orders_table

        create_orders_table(dynamodb=self.dynamodb)

    def update_driver_location(self, driver_id: str, lat: float, lng: float, order_id: str = None):
        now = datetime.now(timezone.utc).isoformat()
        
        # Usamos UpdateItem para inicializar o status se não existir e não sobrescrever se existir
        update_expr = "SET lat = :lat, lng = :lng, updated_at = :now, #s = if_not_exists(#s, :default_status), GSI2PK = if_not_exists(GSI2PK, :default_gsi2pk), GSI2SK = if_not_exists(GSI2SK, :driver_id), driver_id = if_not_exists(driver_id, :driver_id)"
        expr_values = {
            ':lat': str(lat),
            ':lng': str(lng),
            ':now': now,
            ':default_status': DriverStatus.LIVRE.value,
            ':default_gsi2pk': f'DRIVER_STATUS#{DriverStatus.LIVRE.value}',
            ':driver_id': driver_id
        }
        expr_names = {'#s': 'status'}

        if order_id:
            update_expr += ", order_id = :order_id"
            expr_values[':order_id'] = order_id

        response = self.table.update_item(
            Key={'PK': f'DRIVER#{driver_id}', 'SK': 'LATEST'},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW"
        )
        return response.get('Attributes')

    def get_driver_location(self, driver_id: str):
        response = self.table.get_item(Key={'PK': f'DRIVER#{driver_id}', 'SK': 'LATEST'})
        item = response.get('Item')
        if item:
            # Garantir que lat/lng retornem como floats para evitar problemas de coerção
            if 'lat' in item:
                item['lat'] = float(item['lat'])
            if 'lng' in item:
                item['lng'] = float(item['lng'])
        return item

    def get_free_drivers(self, limit: int = 50):
        query_kwargs = {
            'IndexName': 'StatusIndex',
            'KeyConditionExpression': Key('GSI2PK').eq(f'DRIVER_STATUS#{DriverStatus.LIVRE.value}'),
            'Limit': limit,
        }
        response = self.table.query(**query_kwargs)
        items = response.get('Items', [])
        for item in items:
            if 'lat' in item:
                item['lat'] = float(item['lat'])
            if 'lng' in item:
                item['lng'] = float(item['lng'])
        return items

    def batch_update_drivers(self, drivers: list[BatchDriverLocationUpdate]):
        now = datetime.now(timezone.utc).isoformat()
        
        with self.table.batch_writer() as batch:
            for d in drivers:
                status = d.status or DriverStatus.LIVRE
                item = {
                    'PK': f'DRIVER#{d.driver_id}',
                    'SK': 'LATEST',
                    'driver_id': d.driver_id,
                    'lat': str(d.lat),
                    'lng': str(d.lng),
                    'status': status.value,
                    'updated_at': now,
                    'GSI2PK': f'DRIVER_STATUS#{status.value}',
                    'GSI2SK': d.driver_id
                }
                batch.put_item(Item=item)
        return {"status": "success", "count": len(drivers)}
