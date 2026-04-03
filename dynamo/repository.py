import boto3
from datetime import datetime, timezone
import time
import os
import uuid
from .models import OrderCreate, OrderStatus, ORDER_FLOW

class OrderRepository:
    """
    Implementa a lógica do processamento dos pedidos
    """
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

    def _to_dynamo_dict(self, python_dict):
        from boto3.dynamodb.types import TypeSerializer
        serializer = TypeSerializer()
        return {k: serializer.serialize(v) for k, v in python_dict.items()}

class LocationRepository:
    """
    Classe com a lógica de registrar a localização do entregador
    """
    def __init__(self):
        region = os.getenv("AWS_REGION", "us-east-1")
        endpoint_url = os.getenv("DYNAMODB_ENDPOINT_URL")
        self.table_name = os.getenv("DYNAMODB_TABLE_NAME", "DijkfoodOrders")
        
        self.dynamodb = boto3.resource('dynamodb', region_name=region, endpoint_url=endpoint_url)
        self.table = self.dynamodb.Table(self.table_name)

    def update_driver_location(self, driver_id: str, lat: float, lng: float, order_id: str = None):
        """
        Faz o update na localização do entregador
        """
        now = datetime.now(timezone.utc).isoformat()
        ttl = int(time.time() + (2 * 3600)) # 2 horas de retenção

        item = {
            'PK': f'DRIVER#{driver_id}',
            'SK': 'LATEST',
            'driver_id': driver_id,
            'order_id': order_id,
            'lat': str(lat),
            'lng': str(lng),
            'updated_at': now,
            'expiracao': ttl
        }
        
        self.table.put_item(Item=item)
        return item

    def get_driver_location(self, driver_id: str):
        response = self.table.get_item(
            Key={'PK': f'DRIVER#{driver_id}', 'SK': 'LATEST'}
        )
        return response.get('Item')
