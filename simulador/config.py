import os
from dataclasses import dataclass

SCENARIO_RATES: dict[str, float] = {
    "normal": 10.0,
    "peak": 50.0,
    "special": 200.0,
}


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
    scenario: str
    num_workers: int
    run_duration_s: float
    metrics_window_seconds: float

    @property
    def n_drivers(self) -> int:
        return self.n_users * 3

    @property
    def global_req_per_s(self) -> float:
        return SCENARIO_RATES[self.scenario]


def load_config() -> Config:
    scenario = os.getenv("SCENARIO", "normal").strip().lower()
    if scenario not in SCENARIO_RATES:
        raise ValueError(
            f"SCENARIO must be one of {sorted(SCENARIO_RATES)}, got {scenario!r}"
        )
    num_workers = int(os.getenv("NUM_WORKERS", "5"))
    if num_workers < 1:
        raise ValueError(f"NUM_WORKERS must be >= 1, got {num_workers}")
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
        scenario=scenario,
        num_workers=num_workers,
        run_duration_s=float(os.getenv("RUN_DURATION_S", "300")),
        metrics_window_seconds=float(os.getenv("METRICS_WINDOW_SECONDS", "300")),
    )
