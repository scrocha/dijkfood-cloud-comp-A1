"""
isolation_test.py — Teste de isolamento de serviços DijkFood

Demonstra que sobrecarregar o serviço de rotas não afeta cadastro e pedidos.

Fluxo:
  - Fase 1 (0 → RAMP_START): cadastro, pedidos e rotas a 10 req/s
  - Fase 2 (RAMP_START → RAMP_END): rotas sobe linearmente de 10 → 1000 req/s
  - Fase 3 (RAMP_END → DURATION): rotas mantido a 1000 req/s

Endpoints testados:
  - cadastro: GET /cadastro/restaurantes   (leitura real no RDS)
  - pedidos:  GET /pedidos/health          (lightweight, mostra baseline)
  - rotas:    POST /rotas/rota-entrega     (A* CPU-bound, vai saturar)

Saída:
  - artifacts/isolation/isolation_YYYYMMDD_HHMMSS.csv
  - artifacts/isolation/isolation_YYYYMMDD_HHMMSS.png

Uso:
    python isolation_test.py
    python isolation_test.py --duration 120 --ramp-start 20 --ramp-end 80
    python isolation_test.py --output-only artifacts/isolation/isolation_20250420_120000.csv
"""

import argparse
import asyncio
import time
import csv
from pathlib import Path
from collections import defaultdict

import httpx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from carga_unitario.payloads import reload_env_and_urls, rota_entrega
import carga_unitario.payloads as P

# =========================================================================
# PARÂMETROS
# =========================================================================
DURATION   = 120   # segundos totais
RAMP_START = 30    # segundo em que rotas começa a subir
RAMP_END   = 90    # segundo em que rotas chega a 1000 req/s
BASE_RPS   = 10    # req/s baseline (cadastro, pedidos, e rotas na fase 1)
PEAK_RPS   = 1000  # req/s máximo de rotas

ROLLING_WINDOW_S = 3.0   # janela do rolling average no gráfico (segundos)
TIMEOUT_S        = 15.0  # timeout por request (rotas pode demorar)

COLORS = {
    "cadastro": "#2196F3",  # azul
    "pedidos":  "#4CAF50",  # verde
    "rotas":    "#F44336",  # vermelho
}

# =========================================================================
# COLETA
# =========================================================================
_records: list[tuple[float, str, float, bool]] = []   # (t, service, latency_ms, is_error)


async def _fire(client: httpx.AsyncClient, service: str,
                method: str, url: str, **kwargs):
    t0 = time.monotonic()
    try:
        r = await client.request(method, url, **kwargs)
        lat = (time.monotonic() - t0) * 1000
        _records.append((t0, service, lat, r.status_code >= 400))
    except Exception:
        lat = (time.monotonic() - t0) * 1000
        _records.append((t0, service, lat, True))


async def _stream(service: str, method: str, url_fn, rps_fn,
                  deadline: float, client: httpx.AsyncClient, **kwargs):
    """Dispara requisições em cadência definida por rps_fn(t_elapsed)."""
    start = time.monotonic()
    while True:
        now = time.monotonic()
        if now >= deadline:
            break
        elapsed = now - start
        rps = rps_fn(elapsed)
        interval = 1.0 / rps if rps > 0 else 1.0
        asyncio.create_task(_fire(client, service, method, url_fn(), **kwargs))
        await asyncio.sleep(interval)


def _make_rps_fn(service: str, start: float):
    """Retorna função rps(elapsed) para o serviço."""
    def fn(elapsed: float) -> float:
        if service != "rotas":
            return BASE_RPS
        if elapsed < RAMP_START:
            return BASE_RPS
        if elapsed >= RAMP_END:
            return PEAK_RPS
        # ramp linear
        frac = (elapsed - RAMP_START) / (RAMP_END - RAMP_START)
        return BASE_RPS + frac * (PEAK_RPS - BASE_RPS)
    return fn


async def run_test():
    reload_env_and_urls()
    deadline = time.monotonic() + DURATION
    start    = time.monotonic()

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        await asyncio.gather(
            _stream("cadastro", "GET",
                    lambda: f"{P.CADASTRO_URL}/cadastro/restaurantes",
                    _make_rps_fn("cadastro", start), deadline, client),
            _stream("pedidos", "GET",
                    lambda: f"{P.PEDIDOS_URL}/pedidos/health",
                    _make_rps_fn("pedidos", start), deadline, client),
            _stream("rotas", "POST",
                    lambda: f"{P.ROTAS_URL}/rotas/rota-entrega",
                    _make_rps_fn("rotas", start), deadline, client,
                    json=rota_entrega()),
        )


# =========================================================================
# PERSISTÊNCIA
# =========================================================================
def save_csv(path: Path, t0_abs: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "service", "latency_ms", "is_error"])
        for (t, svc, lat, err) in _records:
            w.writerow([f"{t - t0_abs:.3f}", svc, f"{lat:.1f}", int(err)])
    print(f"CSV salvo: {path}")


def load_csv(path: Path):
    """Carrega CSV gerado por save_csv."""
    records = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append((
                float(row["t_s"]),
                row["service"],
                float(row["latency_ms"]),
                row["is_error"] == "1",
            ))
    return records


# =========================================================================
# GRÁFICO
# =========================================================================
def plot(records: list, out_path: Path, duration: int = DURATION):
    by_service: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for (t, svc, lat, err) in records:
        if not err:
            by_service[svc].append((t, lat))

    fig, ax = plt.subplots(figsize=(12, 5))

    for svc, pts in sorted(by_service.items()):
        if not pts:
            continue
        ts  = np.array([p[0] for p in pts])
        lat = np.array([p[1] for p in pts])

        # rolling average: para cada ponto, média dos últimos ROLLING_WINDOW_S segundos
        rolled = np.empty_like(lat)
        for i in range(len(ts)):
            mask = (ts >= ts[i] - ROLLING_WINDOW_S) & (ts <= ts[i])
            rolled[i] = lat[mask].mean()

        ax.plot(ts, rolled, label=svc, color=COLORS.get(svc, "gray"),
                linewidth=1.8, alpha=0.9)

    # anotações de fase
    ax.axvline(RAMP_START, color="gray", linestyle="--", linewidth=1, alpha=0.7)
    ax.axvline(RAMP_END,   color="gray", linestyle="--", linewidth=1, alpha=0.7)
    ax.text(RAMP_START + 1, ax.get_ylim()[1] * 0.95,
            "rotas ramp ↑", fontsize=8, color="gray", va="top")
    ax.text(RAMP_END + 1, ax.get_ylim()[1] * 0.95,
            "rotas 1000 req/s", fontsize=8, color="gray", va="top")

    ax.set_xlabel("Tempo (s)")
    ax.set_ylabel(f"Latência média ({ROLLING_WINDOW_S}s rolling, ms)")
    ax.set_title("Isolamento de serviços — sobrecarga de rotas não afeta cadastro e pedidos")
    ax.legend()
    ax.set_xlim(0, duration)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    print(f"Gráfico salvo: {out_path}")
    plt.close(fig)


# =========================================================================
# MAIN
# =========================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration",    type=int,   default=DURATION)
    parser.add_argument("--ramp-start",  type=int,   default=RAMP_START)
    parser.add_argument("--ramp-end",    type=int,   default=RAMP_END)
    parser.add_argument("--output-only", type=str,   default=None,
                        help="Pula o teste e só plota CSV existente")
    args = parser.parse_args()

    global DURATION, RAMP_START, RAMP_END
    DURATION   = args.duration
    RAMP_START = args.ramp_start
    RAMP_END   = args.ramp_end

    ts       = time.strftime("%Y%m%d_%H%M%S")
    base_dir = Path("artifacts") / "isolation"

    if args.output_only:
        csv_path = Path(args.output_only)
        records  = load_csv(csv_path)
        png_path = csv_path.with_suffix(".png")
        plot(records, png_path, duration=DURATION)
        return

    csv_path = base_dir / f"isolation_{ts}.csv"
    png_path = base_dir / f"isolation_{ts}.png"

    print(f"Iniciando teste de isolamento ({DURATION}s)")
    print(f"  Fase 1 (0–{RAMP_START}s)   : todos a {BASE_RPS} req/s")
    print(f"  Fase 2 ({RAMP_START}–{RAMP_END}s): rotas sobe {BASE_RPS}→{PEAK_RPS} req/s")
    print(f"  Fase 3 ({RAMP_END}–{DURATION}s) : rotas mantido a {PEAK_RPS} req/s")
    print(f"  cadastro e pedidos sempre a {BASE_RPS} req/s\n")

    t0 = time.monotonic()
    asyncio.run(run_test())

    save_csv(csv_path, t0)

    total = len(_records)
    by_svc = defaultdict(lambda: {"ok": 0, "err": 0})
    for (_, svc, _, err) in _records:
        if err:
            by_svc[svc]["err"] += 1
        else:
            by_svc[svc]["ok"] += 1
    for svc, counts in sorted(by_svc.items()):
        print(f"  {svc}: ok={counts['ok']} erros={counts['err']}")
    print(f"  total: {total} requests\n")

    plot(_records, png_path)


if __name__ == "__main__":
    main()
