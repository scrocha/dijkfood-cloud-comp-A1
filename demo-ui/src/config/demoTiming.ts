/** Delays curtos entre passos de “cozinha” (demo). */
export const KITCHEN_STEP_MS = 1800;

/** Cliente pollando pedido. */
export const POLL_ORDER_MS = 2500;

/** Posição do entregador na fase de entrega. */
export const POLL_DRIVER_MS = 1200;

/** Intervalo entre PUTs de localização (demo; simulador usa ~100 ms). */
export const LOCATION_TICK_MS = 200;

export const DELIVERY_SPEED_MPS = 50;
export const DELIVERY_TIME_MULTIPLIER = 1;

/** Fallback se rota retornar 404 (segundos). */
export const FALLBACK_DELIVERY_SEC_MIN = 25;
export const FALLBACK_DELIVERY_SEC_MAX = 45;
