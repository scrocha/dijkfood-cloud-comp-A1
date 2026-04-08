"""
Seed de demonstração — cadastra 3 usuários, 3 restaurantes e 3 entregadores
diretamente na AWS (via ALB).

Uso:
    python seed_aws_demo.py
    ALB_URL=http://outro-alb.amazonaws.com python seed_aws_demo.py
"""

import json
import os
import urllib.request
import urllib.error
from pathlib import Path

def get_alb_url():
    """Tenta obter a URL do ALB do JSON gerado, ou do env, ou fallback fixo."""
    # 1. Tenta alb_endpoints.json na raiz do projeto (onde o deploy.py o salva)
    # Procuramos no CWD ou um nível acima
    root_json = Path("alb_endpoints.json")
    if not root_json.exists():
        root_json = Path(__file__).parent / "alb_endpoints.json"

    if root_json.exists():
        try:
            data = json.loads(root_json.read_text())
            # O sistema usa ALB único, então cadastramos a base do ALB
            url = data.get("cadastro") or data.get("rotas") or data.get("pedidos")
            if url:
                print(f"URL do ALB carregada de {root_json}: {url}")
                return url.rstrip("/")
        except Exception as e:
            print(f"Erro ao ler {root_json}: {e}")

    # 2. Fallback para variável de ambiente ou padrão
    return os.getenv(
        "ALB_URL",
        "http://dijkfood-alb-536088188.us-east-1.elb.amazonaws.com",
    ).rstrip("/")

ALB_URL = get_alb_url()


# ---------------------------------------------------------------------------
# Dados fixos — coordenadas em São Paulo (bairros distintos)
# ---------------------------------------------------------------------------

USUARIOS = [
    {
        "user_id": "demo-usuario-1",
        "primeiro_nome": "Ana",
        "ultimo_nome": "Silva",
        "email": "ana.silva@demo.com",
        "telefone": "11999990001",
        "endereco_latitude": -23.5505,
        "endereco_longitude": -46.6333,
    },
    {
        "user_id": "demo-usuario-2",
        "primeiro_nome": "Bruno",
        "ultimo_nome": "Oliveira",
        "email": "bruno.oliveira@demo.com",
        "telefone": "11999990002",
        "endereco_latitude": -23.5613,
        "endereco_longitude": -46.6563,
    },
    {
        "user_id": "demo-usuario-3",
        "primeiro_nome": "Carla",
        "ultimo_nome": "Mendes",
        "email": "carla.mendes@demo.com",
        "telefone": "11999990003",
        "endereco_latitude": -23.5330,
        "endereco_longitude": -46.6395,
    },
]

RESTAURANTES = [
    {
        "rest_id": "demo-rest-1",
        "nome": "Cantina Bella Napoli",
        "tipo_cozinha": "Italiana",
        "endereco_latitude": -23.5480,
        "endereco_longitude": -46.6388,
    },
    {
        "rest_id": "demo-rest-2",
        "nome": "Temakeria Sakura",
        "tipo_cozinha": "Japonesa",
        "endereco_latitude": -23.5640,
        "endereco_longitude": -46.6520,
    },
    {
        "rest_id": "demo-rest-3",
        "nome": "Burger da Vila",
        "tipo_cozinha": "Hamburgueria",
        "endereco_latitude": -23.5710,
        "endereco_longitude": -46.6250,
    },
]

ENTREGADORES = [
    {
        "entregador_id": "demo-entregador-1",
        "nome": "Diego Rápido",
        "tipo_veiculo": "Moto",
        "endereco_latitude": -23.5490,
        "endereco_longitude": -46.6370,
    },
    {
        "entregador_id": "demo-entregador-2",
        "nome": "Fernanda Veloz",
        "tipo_veiculo": "Bicicleta",
        "endereco_latitude": -23.5600,
        "endereco_longitude": -46.6450,
    },
    {
        "entregador_id": "demo-entregador-3",
        "nome": "Gabriel Cruz",
        "tipo_veiculo": "Moto",
        "endereco_latitude": -23.5550,
        "endereco_longitude": -46.6310,
    },
]


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def post(path: str, body: dict) -> dict:
    url = f"{ALB_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} em {url}: {body_text}") from e


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def seed(label: str, path: str, items: list):
    print(f"\n── {label} ──")
    for item in items:
        key = next(iter(item))  # primeira chave como identificador
        try:
            post(path, item)
            print(f"  ✓  {item[key]}")
        except RuntimeError as e:
            # 409 / duplicate → ignora, dado já existe
            if "409" in str(e) or "already" in str(e).lower() or "duplicate" in str(e).lower():
                print(f"  ~  {item[key]}  (já existe, ignorado)")
            else:
                print(f"  ✗  {item[key]}  →  {e}")


def main():
    print(f"ALB: {ALB_URL}")
    seed("Usuários",     "/cadastro/usuarios",    USUARIOS)
    seed("Restaurantes", "/cadastro/restaurantes", RESTAURANTES)
    seed("Entregadores", "/cadastro/entregadores", ENTREGADORES)
    print("\nSeed concluído.")


if __name__ == "__main__":
    main()
