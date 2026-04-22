from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class GoToRestaurantRequest(BaseModel):
    order_id: str
    courier_id: str
    route: List[Dict[str, float]]  # Lista de pontos {"lat": ..., "lon": ...}
    restaurant: Dict[str, float]
    customer: Dict[str, float]

class OrderReadyRequest(BaseModel):
    order_id: str
    courier_id: str

class GoToClientRequest(BaseModel):
    order_id: str
    courier_id: str
    route: List[Dict[str, float]]
