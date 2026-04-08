import { useEffect, useState } from "react";
import { fetchDriverLocation, type DriverLocationDTO } from "../api/pedidos";
import { ApiError } from "../lib/http";

export function useDriverLocationPolling(
  driverId: string | null,
  intervalMs: number,
  enabled: boolean
) {
  const [loc, setLoc] = useState<DriverLocationDTO | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!driverId || !enabled) {
      setLoc(null);
      setError(null);
      return;
    }

    let cancelled = false;
    const tick = async () => {
      try {
        const l = await fetchDriverLocation(driverId);
        if (!cancelled) {
          setLoc(l);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          if (e instanceof ApiError && e.status === 404) {
            setLoc(null);
            setError(null);
            return;
          }
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
  }, [driverId, intervalMs, enabled]);

  return { loc, error };
}
