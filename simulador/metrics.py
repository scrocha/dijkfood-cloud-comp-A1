import json
import time
from collections import defaultdict
from pathlib import Path

_data: dict[str, list[float]] = defaultdict(list)
_start_time: float = 0.0
_load_phase_active: bool = False
_load_latencies: list[float] = []

_window_data: dict[str, list[float]] = defaultdict(list)
_window_capture_active: bool = False
_window_elapsed_t0: float = 0.0
_window_seq: int = 0
_windows_jsonl_path: Path | None = None
_window_history: list[dict] = []


def start():
    global _start_time
    _start_time = time.monotonic()


def begin_load_phase():
    """Reinicia o relógio e passa a acumular latências só da fase de carga (para status ao vivo)."""
    global _load_phase_active, _load_latencies, _window_elapsed_t0
    start()
    _load_phase_active = True
    _load_latencies = []
    _window_elapsed_t0 = 0.0


def elapsed_s() -> float:
    return time.monotonic() - _start_time


def init_window_persistence(run_dir: Path) -> None:
    global _windows_jsonl_path, _window_seq, _window_history, _window_data
    _windows_jsonl_path = run_dir / "windows.jsonl"
    _window_seq = 0
    _window_history = []
    _window_data = defaultdict(list)


def start_window_capture() -> None:
    global _window_capture_active
    _window_capture_active = True


def stop_window_capture() -> None:
    global _window_capture_active
    _window_capture_active = False


def record(operation: str, latency_ms: float):
    _data[operation].append(latency_ms)
    if _load_phase_active:
        _load_latencies.append(latency_ms)
    if _window_capture_active:
        _window_data[operation].append(latency_ms)


def _window_ops_stats() -> dict:
    ops = {}
    for op, lats in sorted(_window_data.items()):
        n = len(lats)
        if n == 0:
            continue
        sorted_lats = sorted(lats)
        mean = sum(lats) / n
        p95 = sorted_lats[int(n * 0.95)]
        ops[op] = {"count": n, "mean_ms": mean, "p95_ms": p95}
    return ops


def flush_window_jsonl() -> bool:
    """
    Grava uma linha em windows.jsonl com métricas da janela atual e zera _window_data.
    Retorna False se não havia amostras na janela (nada escrito).
    """
    global _window_elapsed_t0, _window_seq
    if _windows_jsonl_path is None:
        return False
    ops = _window_ops_stats()
    if not ops:
        _window_data.clear()
        _window_elapsed_t0 = elapsed_s()
        return False
    t1 = elapsed_s()
    t0 = _window_elapsed_t0
    row = {
        "window_index": _window_seq,
        "t0_elapsed_s": t0,
        "t1_elapsed_s": t1,
        "operations": ops,
    }
    _window_seq += 1
    _window_history.append(row)
    with open(_windows_jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _window_data.clear()
    _window_elapsed_t0 = t1
    return True


def finalize_persistence(summary_meta: dict, run_dir: Path) -> None:
    """Flush janela incompleta (se houver dados) e grava summary.json."""
    if _windows_jsonl_path is not None and _window_data:
        flush_window_jsonl()
    operations_summary = _weighted_operations_summary()
    payload = {
        **summary_meta,
        "operations": operations_summary,
    }
    path = run_dir / "summary.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    stop_window_capture()


def _weighted_operations_summary() -> dict[str, dict]:
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in _window_history:
        for op, st in row["operations"].items():
            c = st["count"]
            m = st["mean_ms"]
            totals[op][0] += c
            totals[op][1] += c * m
    out = {}
    for op, (tot, weighted_sum) in sorted(totals.items()):
        if tot == 0:
            continue
        out[op] = {
            "total_count": tot,
            "weighted_mean_ms": weighted_sum / tot,
        }
    return out


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
