from enum import Enum
from pydantic import BaseModel
from typing import List, Optional

class OrderStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    PREPARING = "PREPARING"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"

ORDER_FLOW = {
    OrderStatus.CONFIRMED: [OrderStatus.PREPARING],
    OrderStatus.PREPARING: [OrderStatus.READY_FOR_PICKUP],
    OrderStatus.READY_FOR_PICKUP: [OrderStatus.PICKED_UP],
    OrderStatus.PICKED_UP: [OrderStatus.IN_TRANSIT],
    OrderStatus.IN_TRANSIT: [OrderStatus.DELIVERED],
    OrderStatus.DELIVERED: []
}

class OrderCreate(BaseModel):
    customer_id: str
    restaurant_id: str
    items: List[dict]
    total_value: float

class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    entregador_id: Optional[str] = None

class DriverLocationUpdate(BaseModel):
    lat: float
    lng: float
    order_id: Optional[str] = None
