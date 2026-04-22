"""
Popular cadastro (Postgres via API) em uma única execução — para Compose profile `seed`.

Variáveis de ambiente:
  CADASTRO_URL       base da API (ex.: http://database-service:8000)
  SEED_USERS         default 20
  SEED_RESTAURANTS   default 8
  SEED_DRIVERS       default 24
  SEED_MAX_WAIT_S    espera máxima pelo /cadastro/health (default 120)
  SEED_WAIT_INTERVAL_S intervalo entre tentativas (default 2)
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time
import uuid

import httpx
from faker import Faker

fake = Faker("pt_BR")

CADASTRO_URL = os.environ.get("CADASTRO_URL", "http://localhost:8002").rstrip("/")
SEED_USERS = int(os.environ.get("SEED_USERS", "500"))
SEED_RESTAURANTS = int(os.environ.get("SEED_RESTAURANTS", "20"))
SEED_DRIVERS = int(os.environ.get("SEED_DRIVERS", "200"))
SEED_MAX_WAIT_S = int(os.environ.get("SEED_MAX_WAIT_S", "120"))
SEED_WAIT_INTERVAL_S = float(os.environ.get("SEED_WAIT_INTERVAL_S", "2"))

LAT_MIN, LAT_MAX = -23.700, -23.400
LON_MIN, LON_MAX = -46.800, -46.400

TIPOS_COZINHA = ["Italiana", "Japonesa", "Brasileira", "Hamburgueria", "Mexicana"]
TIPOS_VEICULO = ["Moto", "Bicicleta", "Carro"]
CARDAPIO = {
    "Italiana": ["Pizza Margherita", "Lasanha Bolonhesa", "Espaguete à Carbonara"],
    "Japonesa": ["Combinado Sushi 20 Peças", "Temaki de Salmão", "Yakisoba de Frango"],
    "Brasileira": ["Feijoada Completa", "Prato Feito", "Porção de Coxinha"],
    "Hamburgueria": ["Hambúrguer Clássico", "Hambúrguer Duplo", "Batata Frita"],
    "Mexicana": ["Taco de Carne", "Burrito Misto", "Nachos com Guacamole"],
}


async def wait_for_cadastro(client: httpx.AsyncClient) -> None:
    url = f"{CADASTRO_URL}/cadastro/health"
    deadline = time.monotonic() + SEED_MAX_WAIT_S
    while time.monotonic() < deadline:
        try:
            r = await client.get(url, timeout=5.0)
            if r.status_code == 200:
                print(f"seed_cadastro: OK {url}", flush=True)
                return
        except httpx.RequestError as e:
            print(f"seed_cadastro: aguardando API ({e})", flush=True)
        await asyncio.sleep(SEED_WAIT_INTERVAL_S)
    print("seed_cadastro: timeout esperando /cadastro/health", flush=True)
    sys.exit(1)


async def main() -> None:
    print(
        f"seed_cadastro: CADASTRO_URL={CADASTRO_URL} "
        f"users={SEED_USERS} restaurantes={SEED_RESTAURANTS} entregadores={SEED_DRIVERS}",
        flush=True,
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        await wait_for_cadastro(client)

        users = [
            {
                "user_id": str(uuid.uuid4()),
                "primeiro_nome": fake.first_name(),
                "ultimo_nome": fake.last_name(),
                "email": f"{uuid.uuid4().hex[:12]}@example.com",
                "telefone": fake.phone_number()[:20],
                "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
                "endereco_longitude": random.uniform(LON_MIN, LON_MAX),
            }
            for _ in range(SEED_USERS)
        ]

        drivers = [
            {
                "entregador_id": str(uuid.uuid4()),
                "nome": fake.name(),
                "tipo_veiculo": random.choice(TIPOS_VEICULO),
                "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
                "endereco_longitude": random.uniform(LON_MIN, LON_MAX),
            }
            for _ in range(SEED_DRIVERS)
        ]

        restaurantes_meta: list[tuple[str, str]] = []
        restaurantes_payload = []
        for _ in range(SEED_RESTAURANTS):
            rid = str(uuid.uuid4())
            cozinha = random.choice(TIPOS_COZINHA)
            restaurantes_meta.append((rid, cozinha))
            restaurantes_payload.append(
                {
                    "rest_id": rid,
                    "nome": fake.company(),
                    "tipo_cozinha": cozinha,
                    "endereco_latitude": random.uniform(LAT_MIN, LAT_MAX),
                    "endereco_longitude": random.uniform(LON_MIN, LON_MAX),
                }
            )

        r1 = await client.post(f"{CADASTRO_URL}/cadastro/batch", json=users)
        print(f"POST /cadastro/batch → {r1.status_code}", flush=True)
        r1.raise_for_status()

        r2 = await client.post(f"{CADASTRO_URL}/cadastro/entregadores/batch", json=drivers)
        print(f"POST /cadastro/entregadores/batch → {r2.status_code}", flush=True)
        r2.raise_for_status()

        r3 = await client.post(f"{CADASTRO_URL}/cadastro/restaurantes/batch", json=restaurantes_payload)
        print(f"POST /cadastro/restaurantes/batch → {r3.status_code}", flush=True)
        r3.raise_for_status()

        produtos: list[dict] = []
        for rid, cozinha in restaurantes_meta:
            pratos = CARDAPIO[cozinha]
            k = min(3, len(pratos))
            for nome in random.sample(pratos, k=k):
                produtos.append({"prod_id": str(uuid.uuid4()), "nome": nome, "rest_id": rid})

        if produtos:
            r4 = await client.post(f"{CADASTRO_URL}/cadastro/produtos/batch", json=produtos)
            print(f"POST /cadastro/produtos/batch → {r4.status_code} ({len(produtos)} itens)", flush=True)
            r4.raise_for_status()

    print("seed_cadastro: concluído.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
