"""Geração de payloads fake + fixture de ids válidos para cenários com dependência."""

import os
import random
from pathlib import Path
from urllib.parse import urlparse
import uuid
from datetime import datetime, timezone

from faker import Faker

_ENV_DIR = Path(__file__).resolve().parent
_ROOT_ENV = _ENV_DIR.parent / ".env"
_PACKAGE_ENV = _ENV_DIR / ".env"
_VITE_TO_URL = {
    "VITE_CADASTRO_URL": "CADASTRO_URL",
    "VITE_ROTAS_URL": "ROTAS_URL",
    "VITE_PEDIDOS_URL": "PEDIDOS_URL",
}
# Chaves vindas do .env mesclado sobrescrevem o shell (evita export antigo tipo SEU-ALB).
_FORCE_FROM_DOTENV = frozenset({
    "CADASTRO_URL", "ROTAS_URL", "PEDIDOS_URL",
    "VITE_CADASTRO_URL", "VITE_ROTAS_URL", "VITE_PEDIDOS_URL",
    "VITE_BACKEND_TARGET",
})


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if (len(val) >= 2 and val[0] == val[-1]) and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def _load_dotenv_merged() -> None:
    """Mescla `.env` na raiz do repo e `carga_unitario/.env` (este sobrescreve chaves do primeiro).

    Para URLs de backend e VITE_*, valores do arquivo **substituem** o shell (prioridade do .env).
    Demais chaves: só entram se não estiverem no ambiente.
    Com VITE_BACKEND_TARGET=docker não propaga VITE_* → CADASTRO_URL (usa padrão localhost).
    """
    merged: dict[str, str] = {}
    if _ROOT_ENV.is_file():
        merged.update(_parse_env_file(_ROOT_ENV))
    if _PACKAGE_ENV.is_file():
        merged.update(_parse_env_file(_PACKAGE_ENV))

    for key, val in merged.items():
        if key in _FORCE_FROM_DOTENV:
            os.environ[key] = val
        elif key not in os.environ:
            os.environ[key] = val

    if os.getenv("VITE_BACKEND_TARGET", "").lower() == "docker":
        return
    for vite_key, url_key in _VITE_TO_URL.items():
        if vite_key in os.environ:
            os.environ[url_key] = os.environ[vite_key]


def _apply_url_constants() -> None:
    global CADASTRO_URL, PEDIDOS_URL, ROTAS_URL
    CADASTRO_URL = os.getenv("CADASTRO_URL", "http://localhost:8002")
    PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8004")
    ROTAS_URL = os.getenv("ROTAS_URL", "http://localhost:8003")


def reload_env_and_urls() -> None:
    """Relê arquivos `.env` e atualiza CADASTRO_URL / ROTAS_URL / PEDIDOS_URL no módulo."""
    _load_dotenv_merged()
    _apply_url_constants()


def urls_source_line() -> str | None:
    parts: list[str] = []
    if _ROOT_ENV.is_file():
        parts.append(str(_ROOT_ENV.resolve()))
    if _PACKAGE_ENV.is_file():
        parts.append(str(_PACKAGE_ENV.resolve()))
    if not parts:
        return None
    return "env file(s): " + " | ".join(parts)


fake = Faker("pt_BR")

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

CADASTRO_URL = "http://localhost:8002"
PEDIDOS_URL = "http://localhost:8004"
ROTAS_URL = "http://localhost:8003"
_load_dotenv_merged()
_apply_url_constants()


def target_environment_line() -> str:
    """Resumo do destino da carga (Docker local vs AWS ALB / outro)."""
    urls = (CADASTRO_URL, ROTAS_URL, PEDIDOS_URL)

    def host_of(u: str) -> str:
        return (urlparse(u).hostname or "").lower()

    def is_local(u: str) -> bool:
        h = host_of(u)
        return h in ("localhost", "127.0.0.1") or h.endswith(".local")

    def is_aws_alb(u: str) -> bool:
        return "amazonaws.com" in host_of(u)

    if all(is_local(u) for u in urls):
        label = "Docker (localhost — portas padrão 8002/8003/8004 no compose)"
    elif all(is_aws_alb(u) for u in urls):
        hn = host_of(CADASTRO_URL) or CADASTRO_URL
        label = f"AWS ALB ({hn})"
    else:
        label = "destino misto ou não reconhecido"

    if len(set(urls)) == 1:
        return f"alvo: {label}  base_url={CADASTRO_URL}"
    return (
        f"alvo: {label}  "
        f"cadastro={CADASTRO_URL}  rotas={ROTAS_URL}  pedidos={PEDIDOS_URL}"
    )


def _coord():
    return random.uniform(LAT_MIN, LAT_MAX), random.uniform(LON_MIN, LON_MAX)


def _point():
    lat, lon = _coord()
    return {"lat": lat, "lon": lon}


# --- Cadastro payloads ---

def usuario():
    lat, lon = _coord()
    return {
        "user_id": str(uuid.uuid4()),
        "primeiro_nome": fake.first_name(),
        "ultimo_nome": fake.last_name(),
        "email": fake.unique.email(),
        "telefone": fake.phone_number()[:20],
        "endereco_latitude": lat,
        "endereco_longitude": lon,
    }


def usuario_batch(n=1):
    return [usuario() for _ in range(n)]


def restaurante():
    lat, lon = _coord()
    return {
        "rest_id": str(uuid.uuid4()),
        "nome": fake.company(),
        "tipo_cozinha": random.choice(TIPOS_COZINHA),
        "endereco_latitude": lat,
        "endereco_longitude": lon,
    }


def restaurante_batch(n=1):
    return [restaurante() for _ in range(n)]


def entregador():
    lat, lon = _coord()
    return {
        "entregador_id": str(uuid.uuid4()),
        "nome": fake.name(),
        "tipo_veiculo": random.choice(TIPOS_VEICULO),
        "endereco_latitude": lat,
        "endereco_longitude": lon,
    }


def entregador_batch(n=1):
    return [entregador() for _ in range(n)]


def produto(rest_id: str):
    cozinha = random.choice(TIPOS_COZINHA)
    nome = random.choice(CARDAPIO[cozinha])
    return {"prod_id": str(uuid.uuid4()), "nome": nome, "rest_id": rest_id}


def produto_batch(rest_id: str, n=1):
    return [produto(rest_id) for _ in range(n)]


# --- Pedidos payloads ---

def order_create(customer_id: str, restaurant_id: str):
    items = [{"nome": f"Item {i}", "quantidade": 1, "preco": round(random.uniform(10, 50), 2)}
             for i in range(random.randint(1, 3))]
    return {
        "customer_id": customer_id,
        "restaurant_id": restaurant_id,
        "items": items,
        "total_value": round(sum(i["preco"] for i in items), 2),
    }


def status_update(status: str, entregador_id: str | None = None):
    body = {"status": status}
    if entregador_id:
        body["entregador_id"] = entregador_id
    return body


def driver_location(order_id: str | None = None):
    lat, lon = _coord()
    body: dict = {"lat": lat, "lng": lon}
    if order_id:
        body["order_id"] = order_id
    return body


# --- Pedido Postgres (cadastro/pedidos) ---

def pedido_postgres(user_id: str, rest_id: str, entregador_id: str):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "pedido_id": str(uuid.uuid4()),
        "user_id": user_id,
        "rest_id": rest_id,
        "entregador_id": entregador_id,
        "confirmed_time": now,
        "preparing_time": now,
        "ready_for_pickup_time": now,
        "picked_up_time": now,
        "in_transit_time": now,
        "delivered_time": now,
    }


# --- Rotas payloads ---

def rota_entrega():
    return {"origem": _point(), "destino": _point()}


def entregador_mais_proximo(n_entregadores=5):
    return {
        "restaurante": _point(),
        "entregadores": [_point() for _ in range(n_entregadores)],
    }


# --- Fixture: cria ids mínimos para cenários que precisam de FK ---

class Fixture:
    """Cria entidades mínimas via API para que cenários com FK funcionem."""

    def __init__(self):
        self.user_id: str | None = None
        self.rest_id: str | None = None
        self.entregador_id: str | None = None

    async def ensure(self, client):
        """Cria 1 usuário, 1 restaurante e 1 entregador se ainda não existirem."""
        import httpx
        if self.user_id:
            return
        u = usuario()
        self.user_id = u["user_id"]
        await client.post(f"{CADASTRO_URL}/cadastro/batch", json=[u], timeout=15)

        r = restaurante()
        self.rest_id = r["rest_id"]
        await client.post(f"{CADASTRO_URL}/cadastro/restaurantes/batch", json=[r], timeout=15)

        e = entregador()
        self.entregador_id = e["entregador_id"]
        await client.post(f"{CADASTRO_URL}/cadastro/entregadores/batch", json=[e], timeout=15)

        p = produto(self.rest_id)
        await client.post(f"{CADASTRO_URL}/cadastro/produtos/batch", json=[p], timeout=15)
