import time
from collections import defaultdict

_data: dict[str, list[float]] = defaultdict(list)
_start_time: float = 0.0
_load_phase_active: bool = False
_load_latencies: list[float] = []


def start():
    global _start_time
    _start_time = time.monotonic()


def begin_load_phase():
    """Reinicia o relógio e passa a acumular latências só da fase de carga (para status ao vivo)."""
    global _load_phase_active, _load_latencies
    start()
    _load_phase_active = True
    _load_latencies = []


def elapsed_s() -> float:
    return time.monotonic() - _start_time


def record(operation: str, latency_ms: float):
    _data[operation].append(latency_ms)
    if _load_phase_active:
        _load_latencies.append(latency_ms)


def snapshot() -> dict[str, tuple[int, float]]:
    """Retorna {operation: (count, mean_ms)} para cada operação."""
    result = {}
    for op, lats in sorted(_data.items()):
        n = len(lats)
        result[op] = (n, sum(lats) / n)
    return result


def total_count() -> int:
    return sum(len(v) for v in _data.values())


def load_phase_count_mean() -> tuple[int, float]:
    n = len(_load_latencies)
    if n == 0:
        return 0, 0.0
    return n, sum(_load_latencies) / n


def end_load_phase():
    global _load_phase_active
    _load_phase_active = False


def print_live_status(suffix: str = ""):
    """Uma linha compacta: chamadas na fase de carga e latência média (atualiza no mesmo lugar com \\r)."""
    n, mean = load_phase_count_mean()
    elapsed = elapsed_s()
    line = f"  Simulador rodando… | {n} chamadas | latência média {mean:.1f} ms | {elapsed:.0f} s{suffix}"
    print(f"\r{line}", end="", flush=True)


def print_summary(title: str = "Métricas"):
    if not _data:
        print("Sem métricas.")
        return

    print(f"\n=== {title} ===")
    print(f"{'Operation':<22} {'Count':>6} {'Mean(ms)':>10} {'P95(ms)':>10}")
    print("-" * 52)
    for op, lats in sorted(_data.items()):
        n = len(lats)
        mean = sum(lats) / n
        p95 = sorted(lats)[int(n * 0.95)]
        print(f"{op:<22} {n:>6} {mean:>10.1f} {p95:>10.1f}")
    print()
