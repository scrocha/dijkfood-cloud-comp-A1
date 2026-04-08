"""Simulador de fluxo completo de pedido (stateful, multi-step, com tasks de background).

Diferente de runner.py (que dispara requests independentes a N RPS), aqui cada
"virtual user" é uma corrotina que executa o ciclo completo de um pedido:

  POST /pedidos/orders
  PATCH .../status PREPARING
  PATCH .../status READY_FOR_PICKUP
  POST /rotas/entregador-mais-proximo  (escolhe o entregador)
  PATCH .../status PICKED_UP (com entregador_id)
  PATCH .../status IN_TRANSIT
  [background] PUT .../drivers/{id}/location  a cada 100ms
  [background] GET .../drivers/{id}/location  a cada 60s
  sleep(180s) -- entrega fixa de 3 minutos
  PATCH .../status DELIVERED

Lança N novos fluxos por segundo (configurável).
"""

import asyncio
import random
import time
import uuid
from collections import defaultdict
from pathlib import Path

import httpx

from .metrics import (
    WindowAccumulator,
    ensure_run_dir,
    write_summary_row,
    write_window_row,
)
from .payloads import (
    CADASTRO_URL,
    PEDIDOS_URL,
    ROTAS_URL,
    entregador as gen_entregador,
    fake,
    order_create,
    reload_env_and_urls,
    restaurante as gen_restaurante,
    status_update,
    target_environment_line,
    urls_source_line,
    usuario as gen_usuario,
    _coord,
)


# ---------- Setup do pool ----------

async def _fetch_or_create(client: httpx.AsyncClient, endpoint: str, batch_endpoint: str,
                            generator, id_key: str, want: int) -> list[dict]:
    """Busca lista existente; se < want, cria os faltantes via batch."""
    try:
        r = await client.get(f"{CADASTRO_URL}{endpoint}", timeout=30)
        existing = r.json() if r.status_code == 200 else []
    except Exception:
        existing = []

    if len(existing) >= want:
        return existing[:want]

    falta = want - len(existing)
    novos = [generator() for _ in range(falta)]
    try:
        await client.post(f"{CADASTRO_URL}{batch_endpoint}", json=novos, timeout=60)
    except Exception as e:
        print(f"[setup] erro ao criar batch {batch_endpoint}: {e}")
    return existing + novos


async def setup_pool(client: httpx.AsyncClient, pool_size: int) -> dict:
    """Carrega/cria um pool de entidades para usar nos fluxos.

    Returns dict com 'users', 'restaurants', 'entregadores' (cada um lista de dicts).
    Restaurantes e entregadores carregam coords (necessárias para /rotas/entregador-mais-proximo).
    """
    print(f"[setup] populando pool (alvo: {pool_size} de cada)")

    users = await _fetch_or_create(
        client, "/cadastro/usuarios", "/cadastro/batch",
        gen_usuario, "user_id", pool_size,
    )
    rests = await _fetch_or_create(
        client, "/cadastro/restaurantes", "/cadastro/restaurantes/batch",
        gen_restaurante, "rest_id", pool_size,
    )
    entregs = await _fetch_or_create(
        client, "/cadastro/entregadores", "/cadastro/entregadores/batch",
        gen_entregador, "entregador_id", pool_size,
    )

    # Normaliza nomes de campos de coords (a API pode retornar com nomes longos)
    def _norm_coords(items: list[dict], lat_key: str, lon_key: str) -> list[dict]:
        out = []
        for it in items:
            lat = it.get(lat_key) or it.get("lat") or _coord()[0]
            lon = it.get(lon_key) or it.get("lon") or _coord()[1]
            it["_lat"] = float(lat)
            it["_lon"] = float(lon)
            out.append(it)
        return out

    rests = _norm_coords(rests, "endereco_latitude", "endereco_longitude")
    entregs = _norm_coords(entregs, "endereco_latitude", "endereco_longitude")

    print(f"[setup] users={len(users)} rests={len(rests)} entregadores={len(entregs)}")
    return {"users": users, "restaurants": rests, "entregadores": entregs}


# ---------- Helpers de request com métrica ----------

async def _timed(client: httpx.AsyncClient, method: str, url: str,
                  json_body=None, slug: str = "", record=None) -> tuple[int, dict | None]:
    """Faz request, mede latência, registra no acumulador. Retorna (status, body)."""
    start = time.monotonic()
    try:
        if method == "POST":
            r = await client.post(url, json=json_body)
        elif method == "PATCH":
            r = await client.patch(url, json=json_body)
        elif method == "PUT":
            r = await client.put(url, json=json_body)
        elif method == "GET":
            r = await client.get(url)
        else:
            raise ValueError(method)
        lat_ms = (time.monotonic() - start) * 1000
        if record is not None:
            if 200 <= r.status_code < 300:
                record(slug, lat_ms, ok=True)
            else:
                record(slug, lat_ms, ok=False)
        try:
            body = r.json()
        except Exception:
            body = None
        return r.status_code, body
    except Exception:
        lat_ms = (time.monotonic() - start) * 1000
        if record is not None:
            record(slug, lat_ms, ok=False)
        return 0, None


# ---------- Loops de background ----------

async def location_update_loop(client: httpx.AsyncClient, driver_id: str,
                                 order_id: str, interval_ms: int, record):
    """Envia PUT de localização do entregador a cada `interval_ms`."""
    interval = interval_ms / 1000.0
    url = f"{PEDIDOS_URL}/pedidos/drivers/{driver_id}/location"
    try:
        while True:
            lat, lon = _coord()
            body = {"lat": lat, "lng": lon, "order_id": order_id}
            await _timed(client, "PUT", url, body, "flow_location_update", record)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return


async def user_track_loop(client: httpx.AsyncClient, driver_id: str,
                            interval_s: float, record):
    """Usuario faz GET da localização do entregador a cada `interval_s`."""
    url = f"{PEDIDOS_URL}/pedidos/drivers/{driver_id}/location"
    try:
        while True:
            await asyncio.sleep(interval_s)
            await _timed(client, "GET", url, None, "flow_user_track", record)
    except asyncio.CancelledError:
        return


# ---------- Fluxo de 1 pedido ----------

async def run_pedido_flow(client: httpx.AsyncClient, pool: dict, cfg: dict, record):
    """Executa o ciclo completo de 1 pedido."""
    flow_start = time.monotonic()

    user = random.choice(pool["users"])
    rest = random.choice(pool["restaurants"])
    user_id = user.get("user_id")
    rest_id = rest.get("rest_id")

    # 1. Cria pedido
    body = order_create(user_id, rest_id)
    status, resp = await _timed(
        client, "POST", f"{PEDIDOS_URL}/pedidos/orders", body,
        "flow_create_order", record,
    )
    if not resp:
        return
    order_id = resp.get("order_id") or resp.get("id")
    if not order_id:
        return

    # 2. PREPARING
    await _timed(
        client, "PATCH", f"{PEDIDOS_URL}/pedidos/orders/{order_id}/status",
        status_update("PREPARING"), "flow_status_update", record,
    )
    await asyncio.sleep(cfg["preparing_delay_s"])

    # 3. READY_FOR_PICKUP
    await _timed(
        client, "PATCH", f"{PEDIDOS_URL}/pedidos/orders/{order_id}/status",
        status_update("READY_FOR_PICKUP"), "flow_status_update", record,
    )

    # 4. Acha entregador mais proximo via rotas service
    sample_size = min(cfg.get("entregador_sample", 10), len(pool["entregadores"]))
    sampled = random.sample(pool["entregadores"], sample_size)
    rota_payload = {
        "restaurante": {"lat": rest["_lat"], "lon": rest["_lon"]},
        "entregadores": [{"lat": e["_lat"], "lon": e["_lon"]} for e in sampled],
    }
    _, rota_resp = await _timed(
        client, "POST", f"{ROTAS_URL}/rotas/entregador-mais-proximo",
        rota_payload, "flow_find_entregador", record,
    )
    if rota_resp and "entregador_idx" in rota_resp:
        idx = rota_resp["entregador_idx"]
        entregador_id = sampled[idx]["entregador_id"]
    else:
        entregador_id = random.choice(pool["entregadores"])["entregador_id"]

    # 5. PICKED_UP (com entregador)
    await _timed(
        client, "PATCH", f"{PEDIDOS_URL}/pedidos/orders/{order_id}/status",
        status_update("PICKED_UP", entregador_id), "flow_status_update", record,
    )
    await asyncio.sleep(cfg["pickup_delay_s"])

    # 6. IN_TRANSIT
    await _timed(
        client, "PATCH", f"{PEDIDOS_URL}/pedidos/orders/{order_id}/status",
        status_update("IN_TRANSIT", entregador_id), "flow_status_update", record,
    )

    # 7. Background tasks rodam durante a entrega (3 minutos fixos)
    loc_task = asyncio.create_task(
        location_update_loop(client, entregador_id, order_id,
                              cfg["location_interval_ms"], record)
    )
    trk_task = asyncio.create_task(
        user_track_loop(client, entregador_id, cfg["track_interval_s"], record)
    )

    try:
        await asyncio.sleep(cfg["delivery_time_s"])
    finally:
        loc_task.cancel()
        trk_task.cancel()
        await asyncio.gather(loc_task, trk_task, return_exceptions=True)

    # 8. DELIVERED
    await _timed(
        client, "PATCH", f"{PEDIDOS_URL}/pedidos/orders/{order_id}/status",
        status_update("DELIVERED"), "flow_status_update", record,
    )

    total_ms = (time.monotonic() - flow_start) * 1000
    record("flow_total", total_ms, ok=True)


# ---------- Orquestrador principal ----------

async def run_flow_test(cfg: dict):
    """Loop principal: spawn N fluxos/segundo durante `duration` segundos."""
    n_per_second = float(cfg["n_per_second"])
    duration = float(cfg["duration"])
    pool_size = int(cfg.get("pool_size", 30))
    timeout_s = float(cfg.get("timeout_s", 60))
    window_s = float(cfg.get("window_seconds", 60))

    cfg.setdefault("preparing_delay_s", 1.0)
    cfg.setdefault("pickup_delay_s", 2.0)
    cfg.setdefault("delivery_time_s", 180.0)
    cfg.setdefault("location_interval_ms", 100)
    cfg.setdefault("track_interval_s", 60.0)
    cfg.setdefault("entregador_sample", 10)

    run_dir = ensure_run_dir(Path("artifacts") / "carga_fluxo")
    src = urls_source_line()
    if src:
        print(src)
    print(target_environment_line())
    print(f"saida {run_dir.resolve()}")
    print(
        f"[fluxo] n_per_second={n_per_second} duration={duration}s pool={pool_size} "
        f"delivery_time={cfg['delivery_time_s']}s loc_interval={cfg['location_interval_ms']}ms"
    )

    accumulators: dict[str, WindowAccumulator] = defaultdict(WindowAccumulator)
    totals: dict[str, dict] = defaultdict(lambda: {"ok": 0, "errors": 0, "lat_sum": 0.0})

    def record(slug: str, latency_ms: float, ok: bool):
        acc = accumulators[slug]
        t = totals[slug]
        if ok:
            acc.record_ok(latency_ms)
            t["ok"] += 1
            t["lat_sum"] += latency_ms
        else:
            acc.record_error()
            t["errors"] += 1

    limits = httpx.Limits(max_connections=2000, max_keepalive_connections=1000)
    async with httpx.AsyncClient(timeout=timeout_s, limits=limits) as client:
        pool = await setup_pool(client, pool_size)
        if not pool["users"] or not pool["restaurants"] or not pool["entregadores"]:
            print("[fluxo] pool insuficiente, abortando")
            return

        spawn_interval = 1.0 / n_per_second if n_per_second > 0 else 1.0
        flows: set[asyncio.Task] = set()
        run_start = time.monotonic()
        window_start = run_start
        deadline = run_start + duration
        minute = 0
        n_spawned = 0

        async def display_loop():
            while True:
                await asyncio.sleep(2.0)
                created = totals.get("flow_create_order", {}).get("ok", 0)
                delivered = sum(
                    1 for slug, t in totals.items()
                    if slug == "flow_total" for _ in [None]
                ) and totals.get("flow_total", {}).get("ok", 0) or 0
                loc_ok = totals.get("flow_location_update", {}).get("ok", 0)
                print(
                    f"[fluxo] spawned={n_spawned} criados_ok={created} "
                    f"finalizados={delivered} loc_updates={loc_ok} "
                    f"flows_ativos={len(flows)}"
                )

        display_task = asyncio.create_task(display_loop())

        try:
            while time.monotonic() < deadline:
                now = time.monotonic()

                # Janela de métricas
                elapsed_window = now - window_start
                if elapsed_window >= window_s:
                    minute += 1
                    for slug, acc in list(accumulators.items()):
                        write_window_row(run_dir, slug, minute, acc, n_per_second, elapsed_window)
                        acc.reset()
                    window_start = time.monotonic()

                # Spawn novo fluxo
                t = asyncio.create_task(run_pedido_flow(client, pool, cfg, record))
                flows.add(t)
                t.add_done_callback(flows.discard)
                n_spawned += 1

                await asyncio.sleep(spawn_interval)

            print(f"[fluxo] janela de spawn encerrada; aguardando {len(flows)} fluxos pendentes")
            if flows:
                await asyncio.gather(*flows, return_exceptions=True)
        finally:
            display_task.cancel()
            try:
                await display_task
            except asyncio.CancelledError:
                pass

        # Flush janela final
        elapsed_window = time.monotonic() - window_start
        if elapsed_window > 0:
            minute += 1
            for slug, acc in accumulators.items():
                if acc.total > 0:
                    write_window_row(run_dir, slug, minute, acc, n_per_second, elapsed_window)

    total_elapsed = time.monotonic() - run_start
    print(f"[fluxo] fim {total_elapsed:.1f}s spawned={n_spawned}")
    for slug, t in sorted(totals.items()):
        total_reqs = t["ok"] + t["errors"]
        mean = t["lat_sum"] / t["ok"] if t["ok"] > 0 else 0.0
        rps_eff = total_reqs / total_elapsed if total_elapsed > 0 else 0.0
        write_summary_row(slug, t["ok"], t["errors"], mean, n_per_second, rps_eff, total_elapsed)
        print(
            f"{slug} ok={t['ok']} erros={t['errors']} lat_ms={mean:.1f} "
            f"rps_eff={rps_eff:.1f}"
        )


def run_flow_from_json(json_path: str):
    """Entry point: lê config JSON e roda o teste de fluxo."""
    import json
    from json import JSONDecodeError
    reload_env_and_urls()

    def _strip_jsonc_comments(s: str) -> str:
        """Remove comentários // e /* */ preservando strings JSON."""
        out: list[str] = []
        i = 0
        n = len(s)
        in_string = False
        escape = False
        while i < n:
            ch = s[i]

            if in_string:
                out.append(ch)
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                i += 1
                continue

            # fora de string
            if ch == '"':
                in_string = True
                out.append(ch)
                i += 1
                continue

            # // comentário até fim da linha
            if ch == "/" and i + 1 < n and s[i + 1] == "/":
                i += 2
                while i < n and s[i] not in "\r\n":
                    i += 1
                continue

            # /* comentário em bloco */
            if ch == "/" and i + 1 < n and s[i + 1] == "*":
                i += 2
                while i + 1 < n and not (s[i] == "*" and s[i + 1] == "/"):
                    i += 1
                i += 2 if i + 1 < n else 0
                continue

            out.append(ch)
            i += 1

        return "".join(out)

    with open(json_path, encoding="utf-8") as f:
        raw = f.read()
    try:
        cfg = json.loads(_strip_jsonc_comments(raw))
    except JSONDecodeError as e:
        raise SystemExit(
            f"[fluxo] erro ao ler JSON em {json_path}: {e.msg} (linha {e.lineno}, coluna {e.colno})"
        ) from e
    asyncio.run(run_flow_test(cfg))
