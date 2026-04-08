"""Mapa de cenários: nome → função async que executa 1 iteração de carga.

Cada cenário retorna dict[str, Result] onde a chave é o slug do endpoint
e Result = (latency_ms: float | None, is_error: bool).
Cenários encadeados (pedidos_flow, cadastro com GET por id) produzem
múltiplas chaves por iteração.
"""

import random
import time
from dataclasses import dataclass

import httpx

from . import payloads as P


@dataclass
class StepResult:
    slug: str
    latency_ms: float
    is_error: bool


async def _req(client: httpx.AsyncClient, method: str, url: str,
               slug: str, **kwargs) -> StepResult:
    t0 = time.monotonic()
    try:
        r = await client.request(method, url, **kwargs)
        lat = (time.monotonic() - t0) * 1000
        return StepResult(slug, lat, r.status_code >= 400)
    except Exception:
        lat = (time.monotonic() - t0) * 1000
        return StepResult(slug, lat, True)


# ── Cadastro ────────────────────────────────────────────────────────────

async def cadastro_health(client, _fix):
    return [await _req(client, "GET", f"{P.CADASTRO_URL}/cadastro/health", "cadastro_health")]


async def cadastro_post_usuario(client, _fix):
    return [await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/usuarios",
                       "cadastro_post_usuario", json=P.usuario())]


async def cadastro_batch(client, _fix):
    return [await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/batch",
                       "cadastro_batch", json=P.usuario_batch(5))]


async def cadastro_get_usuario(client, _fix):
    u = P.usuario()
    results = []
    results.append(await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/batch",
                              "cadastro_batch_setup", json=[u]))
    results.append(await _req(client, "GET",
                              f"{P.CADASTRO_URL}/cadastro/usuarios/{u['user_id']}",
                              "cadastro_get_usuario"))
    return results


async def cadastro_post_restaurante(client, _fix):
    return [await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/restaurantes",
                       "cadastro_post_restaurante", json=P.restaurante())]


async def cadastro_restaurantes_batch(client, _fix):
    return [await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/restaurantes/batch",
                       "cadastro_restaurantes_batch", json=P.restaurante_batch(5))]


async def cadastro_get_restaurantes(client, _fix):
    return [await _req(client, "GET", f"{P.CADASTRO_URL}/cadastro/restaurantes",
                       "cadastro_get_restaurantes")]


async def cadastro_get_restaurante(client, _fix):
    r = P.restaurante()
    results = []
    results.append(await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/restaurantes/batch",
                              "cadastro_rest_batch_setup", json=[r]))
    results.append(await _req(client, "GET",
                              f"{P.CADASTRO_URL}/cadastro/restaurantes/{r['rest_id']}",
                              "cadastro_get_restaurante"))
    return results


async def cadastro_post_entregador(client, _fix):
    return [await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/entregadores",
                       "cadastro_post_entregador", json=P.entregador())]


async def cadastro_entregadores_batch(client, _fix):
    return [await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/entregadores/batch",
                       "cadastro_entregadores_batch", json=P.entregador_batch(5))]


async def cadastro_get_entregadores(client, _fix):
    return [await _req(client, "GET", f"{P.CADASTRO_URL}/cadastro/entregadores",
                       "cadastro_get_entregadores")]


async def cadastro_get_entregador(client, _fix):
    e = P.entregador()
    results = []
    results.append(await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/entregadores/batch",
                              "cadastro_ent_batch_setup", json=[e]))
    results.append(await _req(client, "GET",
                              f"{P.CADASTRO_URL}/cadastro/entregadores/{e['entregador_id']}",
                              "cadastro_get_entregador"))
    return results


async def cadastro_post_produto(client, fix):
    await fix.ensure(client)
    return [await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/produtos",
                       "cadastro_post_produto", json=P.produto(fix.rest_id))]


async def cadastro_produtos_batch(client, fix):
    await fix.ensure(client)
    return [await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/produtos/batch",
                       "cadastro_produtos_batch", json=P.produto_batch(fix.rest_id, 3))]


async def cadastro_post_pedido(client, fix):
    await fix.ensure(client)
    return [await _req(client, "POST", f"{P.CADASTRO_URL}/cadastro/pedidos",
                       "cadastro_post_pedido",
                       json=P.pedido_postgres(fix.user_id, fix.rest_id, fix.entregador_id))]


# ── Pedidos (fluxo encadeado) ───────────────────────────────────────────

async def pedidos_health(client, _fix):
    return [await _req(client, "GET", f"{P.PEDIDOS_URL}/pedidos/health", "pedidos_health")]


async def _req_json(client: httpx.AsyncClient, method: str, url: str,
                    slug: str, **kwargs) -> tuple[StepResult, dict | None]:
    """Como _req mas retorna também o body JSON da resposta."""
    t0 = time.monotonic()
    try:
        r = await client.request(method, url, **kwargs)
        lat = (time.monotonic() - t0) * 1000
        is_err = r.status_code >= 400
        body = r.json() if not is_err else None
        return StepResult(slug, lat, is_err), body
    except Exception:
        lat = (time.monotonic() - t0) * 1000
        return StepResult(slug, lat, True), None


async def pedidos_flow(client, fix):
    """Fluxo completo: create → patch(PREPARING) → get → history → patch(READY) →
    patch(PICKED_UP) → put location → get location → patch(IN_TRANSIT) → patch(DELIVERED) →
    get by customer → get by status."""
    await fix.ensure(client)
    results = []

    body = P.order_create(fix.user_id, fix.rest_id)
    r_create, resp_body = await _req_json(client, "POST", f"{P.PEDIDOS_URL}/pedidos/orders",
                                          "pedidos_post_orders", json=body)
    results.append(r_create)
    if r_create.is_error or not resp_body:
        return results

    order_id = resp_body.get("order_id", "")
    customer_id = fix.user_id
    base = f"{P.PEDIDOS_URL}/pedidos/orders/{order_id}"

    results.append(await _req(client, "PATCH", f"{base}/status",
                              "pedidos_patch_status",
                              json=P.status_update("PREPARING")))

    results.append(await _req(client, "GET", base, "pedidos_get_order"))

    results.append(await _req(client, "GET", f"{base}/history", "pedidos_get_history"))

    results.append(await _req(client, "PATCH", f"{base}/status",
                              "pedidos_patch_status",
                              json=P.status_update("READY_FOR_PICKUP")))

    results.append(await _req(client, "PATCH", f"{base}/status",
                              "pedidos_patch_status",
                              json=P.status_update("PICKED_UP", fix.entregador_id)))

    driver_id = fix.entregador_id
    results.append(await _req(client, "PUT",
                              f"{P.PEDIDOS_URL}/pedidos/drivers/{driver_id}/location",
                              "pedidos_put_location",
                              json=P.driver_location(order_id)))

    results.append(await _req(client, "GET",
                              f"{P.PEDIDOS_URL}/pedidos/drivers/{driver_id}/location",
                              "pedidos_get_location"))

    results.append(await _req(client, "PATCH", f"{base}/status",
                              "pedidos_patch_status",
                              json=P.status_update("IN_TRANSIT", fix.entregador_id)))

    results.append(await _req(client, "PATCH", f"{base}/status",
                              "pedidos_patch_status",
                              json=P.status_update("DELIVERED")))

    results.append(await _req(client, "GET",
                              f"{P.PEDIDOS_URL}/pedidos/orders/customer/{customer_id}",
                              "pedidos_get_by_customer"))

    results.append(await _req(client, "GET",
                              f"{P.PEDIDOS_URL}/pedidos/orders/status/DELIVERED",
                              "pedidos_get_by_status"))

    return results


# ── Rotas ───────────────────────────────────────────────────────────────

async def rotas_health(client, _fix):
    return [await _req(client, "GET", f"{P.ROTAS_URL}/rotas/health", "rotas_health")]


async def rotas_rota_entrega(client, _fix):
    return [await _req(client, "POST", f"{P.ROTAS_URL}/rotas/rota-entrega",
                       "rotas_rota_entrega", json=P.rota_entrega())]


async def rotas_entregador_mais_proximo(client, _fix):
    return [await _req(client, "POST", f"{P.ROTAS_URL}/rotas/entregador-mais-proximo",
                       "rotas_entregador_mais_proximo", json=P.entregador_mais_proximo())]


# ── Registry ────────────────────────────────────────────────────────────

SCENARIOS: dict[str, callable] = {
    "cadastro_health": cadastro_health,
    "cadastro_post_usuario": cadastro_post_usuario,
    "cadastro_batch": cadastro_batch,
    "cadastro_get_usuario": cadastro_get_usuario,
    "cadastro_post_restaurante": cadastro_post_restaurante,
    "cadastro_restaurantes_batch": cadastro_restaurantes_batch,
    "cadastro_get_restaurantes": cadastro_get_restaurantes,
    "cadastro_get_restaurante": cadastro_get_restaurante,
    "cadastro_post_entregador": cadastro_post_entregador,
    "cadastro_entregadores_batch": cadastro_entregadores_batch,
    "cadastro_get_entregadores": cadastro_get_entregadores,
    "cadastro_get_entregador": cadastro_get_entregador,
    "cadastro_post_produto": cadastro_post_produto,
    "cadastro_produtos_batch": cadastro_produtos_batch,
    "cadastro_post_pedido": cadastro_post_pedido,
    "pedidos_health": pedidos_health,
    "pedidos_flow": pedidos_flow,
    "rotas_health": rotas_health,
    "rotas_rota_entrega": rotas_rota_entrega,
    "rotas_entregador_mais_proximo": rotas_entregador_mais_proximo,
}
