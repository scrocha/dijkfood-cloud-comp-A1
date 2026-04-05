from collections import defaultdict

_data: dict[str, list[float]] = defaultdict(list)


def record(operation: str, latency_ms: float):
    _data[operation].append(latency_ms)


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
