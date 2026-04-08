import { useCallback, useState } from "react";
import {
  fetchOrdersByCustomer,
  fetchOrdersByStatus,
  type OrderDTO,
  type OrderStatus,
} from "../api/pedidos";

export function useAdminPedidos() {
  const [byCustomer, setByCustomer] = useState<OrderDTO[] | null>(null);
  const [byStatus, setByStatus] = useState<OrderDTO[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadCustomer = useCallback(async (customerId: string) => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchOrdersByCustomer(customerId);
      setByCustomer(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setByCustomer(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadStatus = useCallback(async (status: OrderStatus) => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchOrdersByStatus(status);
      setByStatus(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setByStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const clearPedidosResults = useCallback(() => {
    setByCustomer(null);
    setByStatus(null);
    setError(null);
  }, []);

  return { byCustomer, byStatus, loading, error, loadCustomer, loadStatus, setError, clearPedidosResults };
}
