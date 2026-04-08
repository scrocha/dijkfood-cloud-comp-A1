import { ApiError } from "../lib/http";
import { patchOrderStatus, putDriverLocation, type OrderStatus } from "../api/pedidos";
import { postRotaEntrega, type Ponto } from "../api/rotas";
import {
  DELIVERY_SPEED_MPS,
  DELIVERY_TIME_MULTIPLIER,
  FALLBACK_DELIVERY_SEC_MAX,
  FALLBACK_DELIVERY_SEC_MIN,
  KITCHEN_STEP_MS,
  LOCATION_TICK_MS,
} from "../config/demoTiming";

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const id = window.setTimeout(() => resolve(), ms);
    const onAbort = () => {
      window.clearTimeout(id);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function patchStatus(
  orderId: string,
  status: OrderStatus,
  entregadorId: string | undefined,
  signal: AbortSignal
): Promise<void> {
  await patchOrderStatus(orderId, {
    status,
    ...(entregadorId ? { entregador_id: entregadorId } : {}),
  });
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
}

function dedupeConsecutive(points: Ponto[]): Ponto[] {
  if (points.length <= 1) return points;
  const out: Ponto[] = [points[0]];
  for (let i = 1; i < points.length; i++) {
    const p = points[i];
    const q = out[out.length - 1];
    if (p.lat !== q.lat || p.lon !== q.lon) out.push(p);
  }
  return out;
}

function polylineFromRota(percursos: RotaEntregaResponsePercursos): Ponto[] {
  const pts: Ponto[] = [];
  for (const seg of percursos) {
    pts.push({ lat: seg.ponto_origem.lat, lon: seg.ponto_origem.lon });
  }
  const last = percursos.at(-1);
  if (last) pts.push({ lat: last.ponto_fim.lat, lon: last.ponto_fim.lon });
  return dedupeConsecutive(pts);
}

type RotaEntregaResponsePercursos = Array<{
  ponto_origem: Ponto;
  ponto_fim: Ponto;
  comprimento: number;
}>;

function fallbackPolyline(origem: Ponto, destino: Ponto, segments: number): Ponto[] {
  const pts: Ponto[] = [];
  for (let i = 0; i <= segments; i++) {
    const u = i / segments;
    pts.push({
      lat: origem.lat + u * (destino.lat - origem.lat),
      lon: origem.lon + u * (destino.lon - origem.lon),
    });
  }
  return pts;
}

function positionOnPolyline(pts: Ponto[], t: number): Ponto {
  if (pts.length === 0) throw new Error("polyline vazia");
  if (pts.length === 1) return pts[0];
  const clamped = Math.min(1, Math.max(0, t));
  const f = clamped * (pts.length - 1);
  const i = Math.min(Math.floor(f), pts.length - 2);
  const u = f - i;
  const p0 = pts[i];
  const p1 = pts[i + 1];
  return {
    lat: p0.lat + u * (p1.lat - p0.lat),
      lon: p0.lon + u * (p1.lon - p0.lon),
  };
}

function deliverySecondsFromDistance(distanciaMetros: number): number {
  const base = distanciaMetros / DELIVERY_SPEED_MPS;
  return Math.max(5, base * DELIVERY_TIME_MULTIPLIER);
}

function randomFallbackSeconds(): number {
  return (
    FALLBACK_DELIVERY_SEC_MIN +
    Math.floor(Math.random() * (FALLBACK_DELIVERY_SEC_MAX - FALLBACK_DELIVERY_SEC_MIN + 1))
  );
}

export type RunDemoPipelineArgs = {
  orderId: string;
  restaurant: Ponto;
  customer: Ponto;
  entregadorId: string;
  signal: AbortSignal;
  onPhase: (phase: string) => void;
};

/**
 * Pipeline serial de PATCH + rota + PUTs + DELIVERED.
 * O poll do pedido corre em paralelo (quem chamou deve manter).
 */
export async function runDemoPipeline(args: RunDemoPipelineArgs): Promise<void> {
  const { orderId, restaurant, customer, entregadorId, signal, onPhase } = args;

  onPhase("Cozinha: preparando");
  await sleep(KITCHEN_STEP_MS, signal);
  await patchStatus(orderId, "PREPARING", undefined, signal);

  onPhase("Cozinha: quase pronto");
  await sleep(KITCHEN_STEP_MS, signal);
  await patchStatus(orderId, "READY_FOR_PICKUP", undefined, signal);

  onPhase("Retirada");
  await sleep(KITCHEN_STEP_MS, signal);
  await patchStatus(orderId, "PICKED_UP", entregadorId, signal);

  onPhase("A caminho (rota)");
  await patchStatus(orderId, "IN_TRANSIT", undefined, signal);

  let polyline: Ponto[];
  let durationSec: number;

  try {
    const rota = await postRotaEntrega(restaurant, customer);
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    polyline = polylineFromRota(rota.dados_rota.percursos);
    if (polyline.length < 2) {
      polyline = fallbackPolyline(restaurant, customer, 24);
    }
    durationSec = deliverySecondsFromDistance(rota.dados_rota.distancia_metros);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    if (e instanceof ApiError && e.status === 404) {
      polyline = fallbackPolyline(restaurant, customer, 24);
      durationSec = randomFallbackSeconds();
    } else {
      throw e;
    }
  }

  const steps = Math.max(2, Math.ceil((durationSec * 1000) / LOCATION_TICK_MS));
  onPhase(`Entrega simulada (~${Math.round(durationSec)}s)`);

  for (let k = 0; k < steps; k++) {
    const t = steps <= 1 ? 1 : k / (steps - 1);
    const pos = positionOnPolyline(polyline, t);
    await putDriverLocation(entregadorId, {
      lat: pos.lat,
      lng: pos.lon,
      order_id: orderId,
    });
    await sleep(LOCATION_TICK_MS, signal);
  }

  onPhase("Entregue");
  await patchStatus(orderId, "DELIVERED", undefined, signal);
}
