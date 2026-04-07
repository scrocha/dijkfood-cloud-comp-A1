import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import metrics
from .config import load_config
from .pedido import run_order
from .populacao import populate


async def main():
    config = load_config()
    r = config.global_req_per_s

    print("=" * 60)
    print("DijkFood Simulador de Carga — Etapas 3–4")
    print("=" * 60)
    print(f"  cadastro : {config.cadastro_url}")
    print(f"  pedidos  : {config.pedidos_url}")
    print(f"  rotas    : {config.rotas_url}")
    print(f"  restaurant_time : {config.restaurant_time_s}s")
    print(f"  delivery_speed  : {config.delivery_speed_mps} m/s")
    print(f"  n_users={config.n_users} | n_drivers={config.n_drivers} | n_restaurants={config.n_restaurants}")
    print(f"  scenario        : {config.scenario} (R={r} pedidos/s)")
    print(f"  num_workers     : {config.num_workers}")
    print(f"  run_duration    : {config.run_duration_s}s")
    print(f"  metrics_window  : {config.metrics_window_seconds}s")
    print()

    if config.startup_wait_s > 0:
        print(f"Aguardando {config.startup_wait_s}s para serviços ficarem prontos...")
        await asyncio.sleep(config.startup_wait_s)
        print()

    async with httpx.AsyncClient(timeout=30.0) as client:
        print("--- FASE DE POPULAÇÃO ---")
        pop = await populate(client, config)
        print(
            f"  Criados: {len(pop.user_ids)} usuários, {len(pop.driver_ids)} entregadores, "
            f"{len(pop.restaurantes)} restaurantes"
        )
        metrics.print_summary("Métricas de População")

        print("--- FASE DE CARGA ---")
        print("(status no terminal: chamadas e média de latência; sem log por requisição)\n")

        run_id = uuid.uuid4().hex
        run_dir = Path("artifacts") / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        metrics.begin_load_phase()
        metrics.init_window_persistence(run_dir)
        metrics.start_window_capture()

        stop = asyncio.Event()
        window_stop = asyncio.Event()
        active_order_tasks: set[asyncio.Task] = set()

        async def live_status_loop():
            interval = 2.0
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    metrics.print_live_status()

        async def window_flush_loop():
            while not window_stop.is_set():
                try:
                    await asyncio.wait_for(
                        window_stop.wait(),
                        timeout=config.metrics_window_seconds,
                    )
                    break
                except asyncio.TimeoutError:
                    metrics.flush_window_jsonl()

        async def worker_loop(worker_index: int):
            n_w = config.num_workers
            interval = n_w / r
            await asyncio.sleep(worker_index / r)
            deadline = time.monotonic() + config.run_duration_s
            while time.monotonic() < deadline:
                t = asyncio.create_task(run_order(client, config, pop))
                active_order_tasks.add(t)
                t.add_done_callback(active_order_tasks.discard)
                await asyncio.sleep(interval)

        load_started_wall = datetime.now(timezone.utc).isoformat()
        status_task = asyncio.create_task(live_status_loop())
        window_task = asyncio.create_task(window_flush_loop())
        worker_tasks = [
            asyncio.create_task(worker_loop(i)) for i in range(config.num_workers)
        ]
        try:
            await asyncio.gather(*worker_tasks)
        finally:
            stop.set()
            window_stop.set()
            await window_task
            await status_task
            metrics.end_load_phase()
            metrics.print_live_status(" — workers encerrados.")
            print()

        load_ended_wall = datetime.now(timezone.utc).isoformat()

        if active_order_tasks:
            await asyncio.gather(*active_order_tasks, return_exceptions=True)

        summary_meta = {
            "run_id": run_id,
            "scenario": config.scenario,
            "R": r,
            "num_workers": config.num_workers,
            "run_duration_s": config.run_duration_s,
            "metrics_window_seconds": config.metrics_window_seconds,
            "load_started_utc": load_started_wall,
            "load_ended_utc": load_ended_wall,
        }
        metrics.finalize_persistence(summary_meta, run_dir)

        print(f"  Artefatos: {run_dir.resolve()}")
        print()
        metrics.print_summary("Métricas Completas")


if __name__ == "__main__":
    asyncio.run(main())
