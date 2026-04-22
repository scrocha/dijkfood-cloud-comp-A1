from typing import Any, Optional
from pydantic import BaseModel


class PrepareOrderRequest(BaseModel):
    order_id: str
    restaurant_id: str
    # Dados extras que o gateway passa — o restaurante ecoa de volta no webhook
    driver_id: Optional[str] = None
    route_to_client: Optional[list[dict[str, Any]]] = None


class RestaurantReadyWebhook(BaseModel):
    order_id: str
    restaurant_id: str
    driver_id: Optional[str] = None
    route_to_client: Optional[list[dict[str, Any]]] = None
