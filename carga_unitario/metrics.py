"""Janela de 60s, cálculo média/rps, escrita CSV por endpoint, print terminal, summary global."""

import csv
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WindowAccumulator:
    """Acumula latências e erros para uma janela de tempo."""
    latencies_ms: list[float] = field(default_factory=list)
    n_errors: int = 0

    def record_ok(self, latency_ms: float):
        self.latencies_ms.append(latency_ms)

    def record_error(self):
        self.n_errors += 1

    @property
    def n_ok(self) -> int:
        return len(self.latencies_ms)

    @property
    def total(self) -> int:
        return self.n_ok + self.n_errors

    @property
    def mean_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return sum(self.latencies_ms) / len(self.latencies_ms)

    def reset(self):
        self.latencies_ms.clear()
        self.n_errors = 0


CSV_HEADER = ["minuto", "latencia_media_ms", "n_erros", "rps_target", "rps_effective"]

SUMMARY_HEADER = [
    "timestamp", "endpoint", "duration_s",
    "total_ok", "total_errors", "latencia_media_ms",
    "rps_target", "rps_effective",
]


def ensure_run_dir(base: Path) -> Path:
    """Cria artifacts/carga/YYYYMMDD_HHMMSS/ e retorna o Path."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    d = base / ts
    d.mkdir(parents=True, exist_ok=True)
    return d


def _csv_path(run_dir: Path, slug: str) -> Path:
    return run_dir / f"{slug}.csv"


def write_window_row(run_dir: Path, slug: str, minute: int, acc: WindowAccumulator,
                     rps_target: float, elapsed_s: float):
    """Append uma linha de janela no CSV do endpoint e imprime no terminal."""
    rps_eff = acc.total / elapsed_s if elapsed_s > 0 else 0.0
    path = _csv_path(run_dir, slug)
    write_header = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(CSV_HEADER)
        w.writerow([minute, f"{acc.mean_ms:.1f}", acc.n_errors,
                     f"{rps_target:.1f}", f"{rps_eff:.1f}"])
    print(
        f"{slug} min={minute} lat_ms={acc.mean_ms:.1f} "
        f"erros={acc.n_errors} rps_tgt={rps_target:.1f} rps_eff={rps_eff:.1f}"
    )


def write_summary_row(slug: str, total_ok: int, total_errors: int,
                      mean_ms: float, rps_target: float, rps_eff: float,
                      duration_s: float):
    """Append uma linha no summary_all.csv global (nunca sobrescreve)."""
    path = Path("artifacts") / "carga" / "summary_all.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(SUMMARY_HEADER)
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"), slug, f"{duration_s:.1f}",
            total_ok, total_errors, f"{mean_ms:.1f}",
            f"{rps_target:.1f}", f"{rps_eff:.1f}",
        ])
