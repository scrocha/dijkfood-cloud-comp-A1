const KEY = "dijkfood_demo_session";

export type DemoSession = {
  userId: string;
  displayName: string;
  lat: number;
  lng: number;
};

export function readSession(): DemoSession | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const o = JSON.parse(raw) as Record<string, unknown>;
    const userId = typeof o.userId === "string" ? o.userId : "";
    const displayName = typeof o.displayName === "string" ? o.displayName : "";
    const lat = Number(o.lat);
    const lng = Number(o.lng);
    if (!userId || !Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    return { userId, displayName, lat, lng };
  } catch {
    return null;
  }
}

export function writeSession(s: DemoSession): void {
  sessionStorage.setItem(KEY, JSON.stringify(s));
}

export function clearSession(): void {
  sessionStorage.removeItem(KEY);
}
