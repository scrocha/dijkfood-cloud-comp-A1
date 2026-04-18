from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CheckoutRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    restaurant_id: str = Field(..., min_length=1)
    items: list[dict[str, Any]] = Field(default_factory=list)
    total_value: float


class RestaurantReadyWebhook(BaseModel):
    order_id: str = Field(..., min_length=1)


class CourierPickedUpWebhook(BaseModel):
    order_id: str = Field(..., min_length=1)
    courier_id: str = Field(..., min_length=1)


class DeliveredWebhook(BaseModel):
    order_id: str = Field(..., min_length=1)
    courier_id: str = Field(..., min_length=1)


class CourierLocationUpdate(BaseModel):
    lat: float
    lng: float
    order_id: Optional[str] = None


class UserCreate(BaseModel):
    first_name: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1)
    lat: float
    lng: float


class RestaurantCreate(BaseModel):
    name: str = Field(..., min_length=1)
    cuisine_type: str = Field(..., min_length=1)
    lat: float
    lng: float


class CourierCreate(BaseModel):
    name: str = Field(..., min_length=1)
    vehicle_type: str = Field(..., min_length=1)
    lat: float
    lng: float
