"""Lê JSON, decide 1 proc vs N subprocesses, loop asyncio de carga."""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

from .metrics import WindowAccumulator, ensure_run_dir, write_summary_row, write_window_row
from .payloads import Fixture
from .scenarios import SCENARIOS


async def run_single_scenario(cfg: dict, run_dir: Path):
    """Executa um cenário: loop asyncio com cadência de RPS, janelas de 60s."""
    name = cfg["scenario"]
    rps = float(cfg["rps"])
    duration = float(cfg["duration"])
    timeout_s = float(cfg.get("timeout_s", 30))
    window_s = float(cfg.get("window_seconds", 60))

    fn = SCENARIOS[name]
    fix = Fixture()

    accumulators: dict[str, WindowAccumulator] = defaultdict(WindowAccumulator)
    totals: dict[str, dict] = defaultdict(lambda: {"ok": 0, "errors": 0, "lat_sum": 0.0})

    interval = 1.0 / rps if rps > 0 else 1.0
    minute = 0
    window_start = time.monotonic()
    run_start = window_start
    completed_in_window = 0

    print(f"[{name}] pid={os.getpid()} rps={rps:.0f} dur={duration:.0f}s")

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        deadline = time.monotonic() + duration
        pending: set[asyncio.Task] = set()

        async def fire_one():
            nonlocal completed_in_window
            step_results = await fn(client, fix)
            for sr in step_results:
                acc = accumulators[sr.slug]
                t = totals[sr.slug]
                if sr.is_error:
                    acc.record_error()
                    t["errors"] += 1
                else:
                    acc.record_ok(sr.latency_ms)
                    t["ok"] += 1
                    t["lat_sum"] += sr.latency_ms
                completed_in_window += 1

        while time.monotonic() < deadline:
            now = time.monotonic()
            elapsed_window = now - window_start
            if elapsed_window >= window_s:
                minute += 1
                for slug, acc in accumulators.items():
                    write_window_row(run_dir, slug, minute, acc, rps, elapsed_window)
                    acc.reset()
                completed_in_window = 0
                window_start = time.monotonic()

            t = asyncio.create_task(fire_one())
            pending.add(t)
            t.add_done_callback(pending.discard)
            await asyncio.sleep(interval)

        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # Flush última janela parcial
        elapsed_window = time.monotonic() - window_start
        if elapsed_window > 0:
            minute += 1
            for slug, acc in accumulators.items():
                if acc.total > 0:
                    write_window_row(run_dir, slug, minute, acc, rps, elapsed_window)

    total_elapsed = time.monotonic() - run_start
    print(f"[{name}] fim {total_elapsed:.1f}s")
    for slug, t in sorted(totals.items()):
        total_reqs = t["ok"] + t["errors"]
        mean = t["lat_sum"] / t["ok"] if t["ok"] > 0 else 0.0
        rps_eff = total_reqs / total_elapsed if total_elapsed > 0 else 0.0
        write_summary_row(slug, t["ok"], t["errors"], mean, rps, rps_eff, total_elapsed)
        print(
            f"{slug} ok={t['ok']} erros={t['errors']} "
            f"lat_ms={mean:.1f} rps_tgt={rps:.1f} rps_eff={rps_eff:.1f}"
        )


def run_from_json(json_path: str, destroy_after: bool = False):
    """Ponto de entrada principal: lê JSON e orquestra."""
    with open(json_path) as f:
        configs = json.load(f)

    if not isinstance(configs, list) or not configs:
        print("Erro: JSON deve ser um array não-vazio de cenários.")
        sys.exit(1)

    for cfg in configs:
        if cfg["scenario"] not in SCENARIOS:
            available = ", ".join(sorted(SCENARIOS))
            print(f"Erro: cenário '{cfg['scenario']}' não existe. Disponíveis: {available}")
            sys.exit(1)

    run_dir = ensure_run_dir(Path("artifacts") / "carga")
    print(f"saida {run_dir.resolve()}")

    if len(configs) == 1:
        print("processos 1")
        asyncio.run(run_single_scenario(configs[0], run_dir))
    else:
        print(f"processos {len(configs)}")
        procs: list[subprocess.Popen] = []
        for cfg in configs:
            cmd = [
                sys.executable, "-m", "carga_unitario",
                "--_internal_single",
                json.dumps(cfg),
                str(run_dir),
            ]
            p = subprocess.Popen(cmd)
            procs.append(p)

        for p in procs:
            p.wait()
        print("fim (todos processos)")

    if destroy_after:
        root = Path(__file__).resolve().parent.parent
        clear_py = root / "clear_data_only.py"
        print(f"Executando limpeza de dados após carga: {clear_py}")
        subprocess.run([sys.executable, str(clear_py)], cwd=str(root))


def run_internal_single(cfg_json: str, run_dir_str: str):
    """Chamado internamente por subprocess para rodar 1 cenário isolado."""
    cfg = json.loads(cfg_json)
    run_dir = Path(run_dir_str)
    asyncio.run(run_single_scenario(cfg, run_dir))
