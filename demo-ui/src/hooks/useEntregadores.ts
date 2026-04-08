import { useCallback, useEffect, useState } from "react";
import { fetchEntregadores, type EntregadorDTO } from "../api/cadastro";

export function useEntregadores() {
  const [list, setList] = useState<EntregadorDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchEntregadores();
      rows.sort((a, b) => a.entregador_id.localeCompare(b.entregador_id));
      setList(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setList([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Primeiro da lista ordenada (contrato da demo). */
  const first = list[0] ?? null;

  return { list, first, loading, error, reload };
}
