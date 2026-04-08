import { useCallback, useState } from "react";
import { ApiError } from "../lib/http";
import { createUsuario, fetchUsuario, fetchUsuarios, type UsuarioDTO } from "../api/cadastro";
import { slugUserId } from "../domain/slugUserId";
import { clearSession, readSession, writeSession, type DemoSession } from "../session/demoSession";

/** Coordenadas dentro da área aceita pelo route_service (SP). */
const DEMO_LAT = -23.56;
const DEMO_LON = -46.66;
const DEMO_PHONE = "11999999999";

export function useAuthByName() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadFromStorage = useCallback((): DemoSession | null => readSession(), []);

  const enter = useCallback(async (displayName: string): Promise<DemoSession> => {
    const trimmed = displayName.trim();
    if (!trimmed) {
      setError("Informe um nome.");
      throw new Error("Informe um nome.");
    }

    const userId = slugUserId(trimmed);
    if (!userId) {
      setError("Nome inválido.");
      throw new Error("Nome inválido.");
    }

    setLoading(true);
    setError(null);

    try {
      let u: UsuarioDTO | null = null;
      try {
        const todos = await fetchUsuarios();
        u = todos.find((x) => x.user_id === userId) ?? null;
      } catch {
        u = await fetchUsuario(userId);
      }

      if (!u) {
        const novo: UsuarioDTO = {
          user_id: userId,
          primeiro_nome: trimmed,
          ultimo_nome: "-",
          email: `${userId}@example.com`,
          telefone: DEMO_PHONE,
          endereco_latitude: DEMO_LAT,
          endereco_longitude: DEMO_LON,
        };
        try {
          await createUsuario(novo);
        } catch (e) {
          if (e instanceof ApiError) {
            setError(e.message);
            throw e;
          }
          throw e;
        }
        u = novo;
      }

      const lat = Number(u.endereco_latitude);
      const lng = Number(u.endereco_longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        const msg = "Coordenadas do usuario inválidas.";
        setError(msg);
        throw new Error(msg);
      }

      const session: DemoSession = {
        userId: u.user_id,
        displayName: trimmed,
        lat,
        lng,
      };
      writeSession(session);
      return session;
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
      setError(msg);
      throw e;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(() => {
    clearSession();
  }, []);

  return { enter, logout, loading, error, setError, loadFromStorage };
}
