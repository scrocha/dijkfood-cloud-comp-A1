import uuid
import random
from dataclasses import dataclass, field

import httpx
from faker import Faker

from .config import Config
from . import http_client as hc

fake = Faker("pt_BR")

LAT_MIN, LAT_MAX = -23.700, -23.400
LON_MIN, LON_MAX = -46.800, -46.400

TIPOS_COZINHA = ["Italiana", "Japonesa", "Brasileira", "Hamburgueria", "Mexicana"]
TIPOS_VEICULO = ["Moto", "Bicicleta", "Carro"]
CARDAPIO = {
    "Italiana":     ["Pizza Margherita", "Lasanha Bolonhesa", "Espaguete à Carbonara"],
    "Japonesa":     ["Combinado Sushi 20 Peças", "Temaki de Salmão", "Yakisoba de Frango"],
    "Brasileira":   ["Feijoada Completa", "Prato Feito", "Porção de Coxinha"],
    "Hamburgueria": ["Hambúrguer Clássico", "Hambúrguer Duplo", "Batata Frita"],
    "Mexicana":     ["Taco de Carne", "Burrito Misto", "Nachos com Guacamole"],
}


@dataclass
class RestInfo:
    id: str
    lat: float
    lon: float
    produtos: list[dict] = field(default_factory=list)


@dataclass
class PopData:
    user_ids: list[str]
    user_coords: list[tuple[float, float]]
    driver_ids: list[str]
    restaurantes: list[RestInfo]


async def populate(client: httpx.AsyncClient, config: Config) -> PopData:
    # --- Usuários ---
    users = [
        {
            "user_id": str(uuid.uuid4()),
            "primeiro_nome": fake.first_name(),
            "ultimo_nome": fake.last_name(),
            "email": fake.unique.email(),
            "telefone": fake.phone_number()[:20],
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX),
        }
        for _ in range(config.n_users)
    ]
    await hc.request(client, "POST", f"{config.cadastro_url}/cadastro/batch", "pop_usuarios", json=users)

    # --- Entregadores ---
    drivers = [
        {
            "entregador_id": str(uuid.uuid4()),
            "nome": fake.name(),
            "tipo_veiculo": random.choice(TIPOS_VEICULO),
            "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
            "endereco_longitude": random.uniform(LON_MIN, LON_MAX),
        }
        for _ in range(config.n_drivers)
    ]
    await hc.request(client, "POST", f"{config.cadastro_url}/cadastro/entregadores/batch", "pop_entregadores", json=drivers)

    # --- Restaurantes ---
    restaurantes_payload = []
    restaurantes_info: list[RestInfo] = []
    for _ in range(config.n_restaurants):
        rid = str(uuid.uuid4())
        cozinha = random.choice(TIPOS_COZINHA)
        lat = random.uniform(LAT_MIN, LAT_MAX)
        lon = random.uniform(LON_MIN, LON_MAX)
        produtos = [
            {"prod_id": str(uuid.uuid4()), "nome": p, "rest_id": rid}
            for p in random.sample(CARDAPIO[cozinha], k=3)
        ]
        restaurantes_payload.append({
            "rest_id": rid,
            "nome": fake.company(),
            "tipo_cozinha": cozinha,
            "endereco_latitude": lat,
            "endereco_longitude": lon,
        })
        restaurantes_info.append(RestInfo(id=rid, lat=lat, lon=lon, produtos=produtos))

    await hc.request(client, "POST", f"{config.cadastro_url}/cadastro/restaurantes/batch", "pop_restaurantes", json=restaurantes_payload)

    # --- Produtos ---
    todos_produtos = [p for r in restaurantes_info for p in r.produtos]
    await hc.request(client, "POST", f"{config.cadastro_url}/cadastro/produtos/batch", "pop_produtos", json=todos_produtos)

    return PopData(
        user_ids=[u["user_id"] for u in users],
        user_coords=[(u["endereco_latitude"], u["endereco_longitude"]) for u in users],
        driver_ids=[d["entregador_id"] for d in drivers],
        restaurantes=restaurantes_info,
    )
