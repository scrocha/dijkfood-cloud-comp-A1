import { useEffect, useState } from "react";
import { fetchOrder, type OrderDTO } from "../api/pedidos";

export function useOrderPolling(orderId: string | null, intervalMs: number, enabled: boolean) {
  const [order, setOrder] = useState<OrderDTO | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!orderId || !enabled) {
      setOrder(null);
      setError(null);
      return;
    }

    let cancelled = false;
    const tick = async () => {
      try {
        const o = await fetchOrder(orderId);
        if (!cancelled) {
          setOrder(o);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    };

    void tick();
    const id = window.setInterval(() => void tick(), intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [orderId, intervalMs, enabled]);

  return { order, error };
}
