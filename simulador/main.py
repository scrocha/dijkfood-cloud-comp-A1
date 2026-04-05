import asyncio

import httpx

from .config import load_config
from .populacao import populate
from .pedido import run_order
from . import metrics


async def main():
    config = load_config()

    print("=" * 60)
    print("DijkFood Simulador de Carga — Fase 1")
    print("=" * 60)
    print(f"  cadastro : {config.cadastro_url}")
    print(f"  pedidos  : {config.pedidos_url}")
    print(f"  rotas    : {config.rotas_url}")
    print(f"  restaurant_time : {config.restaurant_time_s}s")
    print(f"  delivery_speed  : {config.delivery_speed_mps} m/s")
    print(f"  n_users={config.n_users} | n_drivers={config.n_drivers} | n_restaurants={config.n_restaurants}")
    print()

    if config.startup_wait_s > 0:
        print(f"Aguardando {config.startup_wait_s}s para serviços ficarem prontos...")
        await asyncio.sleep(config.startup_wait_s)
        print()

    async with httpx.AsyncClient(timeout=30.0) as client:
        print("--- FASE DE POPULAÇÃO ---")
        pop = await populate(client, config)
        print(f"  Criados: {len(pop.user_ids)} usuários, {len(pop.driver_ids)} entregadores, {len(pop.restaurantes)} restaurantes")
        metrics.print_summary("Métricas de População")

        print("--- FASE DE CARGA (1 pedido) ---")
        order_id = await run_order(client, config, pop)
        print(f"\n  Pedido {order_id} finalizado como DELIVERED.")
        metrics.print_summary("Métricas Completas")


if __name__ == "__main__":
    asyncio.run(main())
