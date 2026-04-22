from pydantic import BaseModel
from typing import List, Optional

class StartSimulationRequest(BaseModel):
    rate: float = 50.0  # pedidos por minuto ou intervalo configurável

class ItemPedido(BaseModel):
    produto_id: str
    quantidade: int

class CheckoutRequest(BaseModel):
    customer_id: str
    restaurant_id: str
    items: List[ItemPedido]
    total_value: float
