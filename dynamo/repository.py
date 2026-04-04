import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone
import uuid
import time
import os
import httpx
from decimal import Decimal
from .models import OrderCreate, OrderStatus, ORDER_FLOW

class OrderRepository:
    def __init__(self):
        region = os.getenv("AWS_REGION", "us-east-1")
        endpoint_url = os.getenv("DYNAMODB_ENDPOINT_URL")
        self.table_name = os.getenv("DYNAMODB_TABLE_NAME", "DijkfoodOrders")
        
        self.dynamodb = boto3.resource('dynamodb', region_name=region, endpoint_url=endpoint_url)
        self.table = self.dynamodb.Table(self.table_name)
        self.client = boto3.client('dynamodb', region_name=region, endpoint_url=endpoint_url)

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
        if entregador_id:
            update_expr += ", entregador_id = :entregador_id"
            expr_attr_values[':entregador_id'] = entregador_id

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
        self.client.transact_write_items(TransactItems=transact_items)

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

            # URL do serviço de banco de dados conforme docker-compose
            db_url = "http://database-service:8000/cadastro/pedidos"
            
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

    def update_driver_location(self, driver_id: str, lat: float, lng: float, order_id: str = None):
        now = datetime.now(timezone.utc).isoformat()
        item = {
            'PK': f'DRIVER#{driver_id}',
            'SK': 'LATEST',
            'driver_id': driver_id,
            'order_id': order_id,
            'lat': str(lat),
            'lng': str(lng),
            'updated_at': now
        }
        self.table.put_item(Item=item)
        return item

    def get_driver_location(self, driver_id: str):
        response = self.table.get_item(Key={'PK': f'DRIVER#{driver_id}', 'SK': 'LATEST'})
        return response.get('Item')
