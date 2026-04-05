import { useCallback, useEffect, useRef, useState } from "react";
import { createOrder, type OrderItem } from "../api/pedidos";
import type { Ponto } from "../api/rotas";
import { runDemoPipeline } from "../domain/orderSimulation";

export type RunDemoParams = {
  customerId: string;
  restaurantId: string;
  items: OrderItem[];
  totalValue: number;
  restaurant: Ponto;
  customer: Ponto;
  entregadorId: string;
  onOrderCreated: (orderId: string) => void;
};

export function useRunOrderDemo() {
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const runDemo = useCallback(async (params: RunDemoParams): Promise<void> => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setBusy(true);
    setError(null);
    setPhase("");

    try {
      const order = await createOrder({
        customer_id: params.customerId,
        restaurant_id: params.restaurantId,
        items: params.items,
        total_value: params.totalValue,
      });
      params.onOrderCreated(order.order_id);

      await runDemoPipeline({
        orderId: order.order_id,
        restaurant: params.restaurant,
        customer: params.customer,
        entregadorId: params.entregadorId,
        signal: ac.signal,
        onPhase: setPhase,
      });
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  return { runDemo, cancel, phase, busy, error, setError };
}
