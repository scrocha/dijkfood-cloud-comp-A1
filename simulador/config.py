import os
from dataclasses import dataclass


@dataclass
class Config:
    cadastro_url: str
    pedidos_url: str
    rotas_url: str
    restaurant_time_s: float
    delivery_speed_mps: float
    delivery_time_multiplier: float
    n_users: int
    n_restaurants: int
    startup_wait_s: int

    @property
    def n_drivers(self) -> int:
        return self.n_users * 3


def load_config() -> Config:
    return Config(
        cadastro_url=os.getenv("CADASTRO_URL", "http://localhost:8002"),
        pedidos_url=os.getenv("PEDIDOS_URL", "http://localhost:8004"),
        rotas_url=os.getenv("ROTAS_URL", "http://localhost:8003"),
        restaurant_time_s=float(os.getenv("SIMULADOR_RESTAURANT_TIME_S", "30")),
        delivery_speed_mps=float(os.getenv("SIMULADOR_DELIVERY_SPEED_MPS", "5.0")),
        delivery_time_multiplier=float(os.getenv("SIMULADOR_DELIVERY_TIME_MULTIPLIER", "1.0")),
        n_users=int(os.getenv("N_USERS", "10")),
        n_restaurants=int(os.getenv("N_RESTAURANTS", "5")),
        startup_wait_s=int(os.getenv("STARTUP_WAIT_S", "10")),
    )
