import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone
import uuid
import time
import os
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

    def update_status(self, order_id: str, next_status: OrderStatus):
        now = datetime.now(timezone.utc).isoformat()
        order = self.get_order(order_id)
        if not order:
            raise ValueError("Pedido não encontrado")
        
        current_status = OrderStatus(order['status'])
        if next_status not in ORDER_FLOW.get(current_status, []):
            raise ValueError(f"Transição de {current_status} para {next_status} não é permitida.")

        transact_items = [
            {
                'Update': {
                    'TableName': self.table_name,
                    'Key': {'PK': {'S': f'ORDER#{order_id}'}, 'SK': {'S': 'METADATA'}},
                    'UpdateExpression': "SET #s = :new_status, updated_at = :now, GSI2PK = :new_gsi2pk",
                    'ConditionExpression': "#s = :current_status",
                    'ExpressionAttributeNames': {'#s': 'status'},
                    'ExpressionAttributeValues': self._to_dynamo_dict({
                        ':new_status': next_status.value,
                        ':current_status': current_status.value,
                        ':now': now,
                        ':new_gsi2pk': f'STATUS#{next_status.value}'
                    })
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
        return True

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

    def _to_dynamo_dict(self, python_dict):
        from boto3.dynamodb.types import TypeSerializer
        serializer = TypeSerializer()
        return {k: serializer.serialize(v) for k, v in python_dict.items()}

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
