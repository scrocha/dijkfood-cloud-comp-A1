from pydantic import BaseModel

class PrepareOrderRequest(BaseModel):
    order_id: str
    restaurant_id: str

class RestaurantReadyWebhook(BaseModel):
    order_id: str
    restaurant_id: str
