import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .models import OrderCreate, OrderStatus, OrderStatusUpdate, DriverLocationUpdate, BatchDriverLocationUpdate
from .repository import OrderRepository, LocationRepository
from typing import List, Optional

app = FastAPI(
    title="Dijkfood Order Service (DynamoDB)"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
order_repo = OrderRepository()
loc_repo = LocationRepository()

@app.get("/pedidos/health")
def health():
    return {"status": "healthy"}

# --- Endpoints de Pedidos ---

@app.post("/pedidos/orders", status_code=201)
def create_order(order: OrderCreate):
    try:
        return order_repo.create_order(order)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pedidos/orders/{order_id}")
def get_order(order_id: str):
    order = order_repo.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return order

@app.get("/pedidos/orders/{order_id}/history")
def get_order_history(order_id: str):
    return order_repo.get_order_history(order_id)

@app.patch("/pedidos/orders/{order_id}/status")
def update_status(order_id: str, update: OrderStatusUpdate):
    try:
        success = order_repo.update_status(order_id, update.status, update.entregador_id)
        if not success:
            raise HTTPException(status_code=400, detail="Erro ao atualizar status.")
        return {"message": f"Status atualizado para {update.status}"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pedidos/orders/customer/{customer_id}")
def list_by_customer(customer_id: str):
    return order_repo.list_by_customer(customer_id)

@app.get("/pedidos/orders/status/{status}")
def list_by_status(status: OrderStatus):
    return order_repo.list_by_status(status.value)

# --- Endpoints de Rastreamento (Driver Tracking) ---

@app.get("/pedidos/drivers/status/free")
def list_free_drivers(limit: int = 50):
    try:
        return loc_repo.get_free_drivers(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/pedidos/drivers/{driver_id}/location")
def update_location(driver_id: str, location: DriverLocationUpdate):
    try:
        return loc_repo.update_driver_location(
            driver_id, location.lat, location.lng, location.order_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pedidos/drivers/batch-location")
def batch_update_location(drivers: List[BatchDriverLocationUpdate]):
    try:
        return loc_repo.batch_update_drivers(drivers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/pedidos/drivers/{driver_id}/location")
def get_location(driver_id: str):
    loc = loc_repo.get_driver_location(driver_id)
    if not loc:
        raise HTTPException(status_code=404, detail="Localização não encontrada")
    return loc
