import { useCallback, useEffect, useState } from "react";
import { fetchRestaurantes, type RestauranteDTO } from "../api/cadastro";

export function useRestaurants() {
  const [list, setList] = useState<RestauranteDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchRestaurantes();
      rows.sort((a, b) => a.rest_id.localeCompare(b.rest_id));
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

  return { list, loading, error, reload };
}
