import { useCallback, useEffect, useState } from "react";
import { fetchUsuarios, type UsuarioDTO } from "../api/cadastro";

export function useUsuarios() {
  const [list, setList] = useState<UsuarioDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchUsuarios();
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
