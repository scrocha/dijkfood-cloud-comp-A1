import { useEffect, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import type { OrderStatus } from "../api/pedidos";
import { postRotaEntrega, type RotaEntregaResponse } from "../api/rotas";
import { DriverMap } from "../components/DriverMap";
import { POLL_DRIVER_MS, POLL_ORDER_MS } from "../config/demoTiming";
import { MOCK_PRODUCTS } from "../config/mockProducts";
import { useDriverLocationPolling } from "../hooks/useDriverLocationPolling";
import { useEntregadores } from "../hooks/useEntregadores";
import { useOrderPolling } from "../hooks/useOrderPolling";
import { useRestaurants } from "../hooks/useRestaurants";
import { useRunOrderDemo } from "../hooks/useRunOrderDemo";
import { clearSession, readSession, type DemoSession } from "../session/demoSession";

/** Só faz sentido mostrar/poller GPS depois disto — antes ainda é cozinha. */
const DRIVER_TRACKING_STATUSES: OrderStatus[] = [
  "READY_FOR_PICKUP",
  "PICKED_UP",
  "IN_TRANSIT",
  "DELIVERED",
];


export function AppPage() {
  /** Sincronizar com sessionStorage na 1ª renderização; useEffect chega tarde demais para Navigate. */
  const [session, setSession] = useState<DemoSession | null>(() => readSession());
  const [restId, setRestId] = useState("");
  const [productIdx, setProductIdx] = useState(0);
  const [orderId, setOrderId] = useState<string | null>(null);
  const [route, setRoute] = useState<RotaEntregaResponse | null>(null);

  const { list: restaurants, loading: lr, error: er } = useRestaurants();
  const { list: entregadores, first: entregador, loading: le, error: ee } = useEntregadores();
  const { runDemo, phase, busy, error: simErr, setError: setSimErr, cancel } = useRunOrderDemo();

  const { order, error: pollErr } = useOrderPolling(orderId, POLL_ORDER_MS, !!orderId);

  // Busca rota quando restaurante é selecionado ou troca
  useEffect(() => {
    if (session && restId && restaurants.length > 0) {
      const r = restaurants.find(res => res.rest_id === restId);
      if (r) {
        postRotaEntrega(
          { lat: r.endereco_latitude, lon: r.endereco_longitude },
          { lat: session.lat, lon: session.lng }
        ).then(setRoute).catch(console.error);
      }
    } else {
      setRoute(null);
    }
  }, [restId, restaurants, session]);

  /**
   * Nome/coords do entregador só após READY_FOR_PICKUP (cozinha não envolve entregador na demo).
   * Com `order` mas CONFIRMED/PREPARING → "—", igual ao GPS.
   */
  const showEntregadorNoAcompanhamento =
    !!orderId && !!order && DRIVER_TRACKING_STATUSES.includes(order.status);
  const effectiveEntregadorId = showEntregadorNoAcompanhamento
    ? order!.entregador_id ?? entregador?.entregador_id ?? null
    : null;
  const entregadorDaVista = useMemo(() => {
    if (!effectiveEntregadorId) return null;
    return entregadores.find((e) => e.entregador_id === effectiveEntregadorId) ?? null;
  }, [entregadores, effectiveEntregadorId]);

  const { loc: driverLoc } = useDriverLocationPolling(
    effectiveEntregadorId,
    POLL_DRIVER_MS,
    !!effectiveEntregadorId
  );

  const routePoints = useMemo(() => {
    if (!route) return [];
    const pts: { lat: number; lng: number }[] = [];
    route.dados_rota.percursos.forEach(p => {
      pts.push({ lat: p.ponto_origem.lat, lng: p.ponto_origem.lon });
      pts.push({ lat: p.ponto_fim.lat, lng: p.ponto_fim.lon });
    });
    return pts;
  }, [route]);

  /**
   * Cálculo da distância Euclidiana simples para estimativa de progresso na rota.
   * Em uma aplicação real, usaríamos a projeção do ponto no grafo.
   */
  const remainingDistance = useMemo(() => {
    if (!route || !order || !driverLoc) return null;
    if (order.status === "DELIVERED") return 0;

    const dLat = Number(driverLoc.lat);
    const dLng = Number(driverLoc.lng);
    const percursos = route.dados_rota.percursos;

    if (percursos.length === 0) return 0;

    // Encontra o segmento mais próximo do entregador
    let minIdx = 0;
    let minDist = Infinity;

    for (let i = 0; i < percursos.length; i++) {
      // Distância do entregador ao início do segmento
      const lat = percursos[i].ponto_origem.lat;
      const lon = percursos[i].ponto_origem.lon;
      const dist = Math.sqrt(Math.pow(lat - dLat, 2) + Math.pow(lon - dLng, 2));
      if (dist < minDist) {
        minDist = dist;
        minIdx = i;
      }
    }

    // Soma as distâncias de todos os segmentos a partir do atual
    let totalRestante = 0;
    for (let i = minIdx; i < percursos.length; i++) {
      totalRestante += percursos[i].comprimento;
    }

    return totalRestante;
  }, [route, order, driverLoc]);

  const formatDistance = (metros: number | null) => {
    if (metros === null) return "—";
    if (metros >= 1000) return `${(metros / 1000).toFixed(2)} km`;
    return `${Math.round(metros)} m`;
  };

  const restaurant = useMemo(() => restaurants.find((r) => r.rest_id === restId), [restaurants, restId]);

  useEffect(() => {
    if (!restId && restaurants.length > 0) {
      setRestId(restaurants[0].rest_id);
    }
  }, [restaurants, restId]);

  function logout() {
    cancel();
    clearSession();
    setSession(null);
    setOrderId(null);
  }

  if (!session) {
    return <Navigate to="/" replace />;
  }

  const sess = session;
  const mock = MOCK_PRODUCTS[productIdx] ?? MOCK_PRODUCTS[0];
  const canOrder = !!restaurant && !!entregador && !lr && !le && !busy;

  async function onPedido(e: React.FormEvent) {
    e.preventDefault();
    setSimErr(null);
    if (!restaurant || !entregador) return;

    const items = [
      {
        prod_id: mock.id,
        nome: mock.label,
        qtd: 1,
      },
    ];

    setOrderId(null);

    await runDemo({
      customerId: sess.userId,
      restaurantId: restaurant.rest_id,
      items,
      totalValue: mock.unitPrice,
      restaurant: { lat: restaurant.endereco_latitude, lon: restaurant.endereco_longitude },
      customer: { lat: sess.lat, lon: sess.lng },
      entregadorId: entregador.entregador_id,
      entregadorPos: { lat: entregador.endereco_latitude, lon: entregador.endereco_longitude },
      onOrderCreated: (id) => setOrderId(id),
    });
  }

  return (
    <>
      <h1>Fazer pedido</h1>
      <p className="muted">
        Olá, {sess.displayName}.{" "}
        <button type="button" className="secondary" onClick={logout}>
          Sair
        </button>
      </p>

      {(er || ee) && (
        <p className="err">
          {er ?? ee}
        </p>
      )}

      {!entregador && !le && (
        <p className="err">Cadastre pelo menos um entregador (Admin). Sem entregador não há pedido.</p>
      )}

      <form className="card" onSubmit={onPedido}>
        <label htmlFor="rest">Restaurante</label>
        <select
          id="rest"
          value={restId}
          onChange={(e) => setRestId(e.target.value)}
          disabled={lr || restaurants.length === 0}
        >
          {restaurants.map((r) => (
            <option key={r.rest_id} value={r.rest_id}>
              {r.nome} ({r.rest_id})
            </option>
          ))}
        </select>

        <label htmlFor="prod">Item (demo)</label>
        <select id="prod" value={productIdx} onChange={(e) => setProductIdx(Number(e.target.value))}>
          {MOCK_PRODUCTS.map((p, i) => (
            <option key={p.id} value={i}>
              {p.label} — R$ {p.unitPrice.toFixed(2)}
            </option>
          ))}
        </select>

        {simErr ? <p className="err">{simErr}</p> : null}

        <button type="submit" disabled={!canOrder}>
          {busy ? "Simulando…" : "Fazer pedido"}
        </button>
      </form>

      <div className="card">
        <h2>Acompanhamento</h2>
        {pollErr ? <p className="err">{pollErr}</p> : null}

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
          <div>
            <p>
              <span className="muted">Pedido</span>{" "}
              <span className="mono">{orderId ?? "—"}</span>
            </p>
            <p>
              <span className="muted">Status (API)</span>{" "}
              <strong style={{ color: "var(--primary)" }}>{order?.status ?? "—"}</strong>
            </p>
            <p>
              <span className="muted">Fase (simulação)</span>{" "}
              {phase || "—"}
            </p>
          </div>
          <div>
            <p>
              <span className="muted">Entregador</span>{" "}
              {entregadorDaVista
                ? `${entregadorDaVista.nome}`
                : effectiveEntregadorId
                  ? `ID: ${effectiveEntregadorId}`
                  : "—"}
            </p>
            {remainingDistance !== null && (
              <p>
                <span className="muted">Distância restante</span>{" "}
                <strong>{formatDistance(remainingDistance)}</strong>
              </p>
            )}
          </div>
        </div>

        <DriverMap
          driver={driverLoc ? { lat: Number(driverLoc.lat), lng: Number(driverLoc.lng) } : null}
          restaurant={restaurant ? { lat: restaurant.endereco_latitude, lng: restaurant.endereco_longitude } : null}
          customer={{ lat: sess.lat, lng: sess.lng }}
          driverName={entregadorDaVista?.nome}
          restaurantName={restaurant?.nome}
          routePoints={routePoints}
        />
      </div>

      <p className="muted">
        <Link to="/admin">Admin</Link> — cadastre restaurantes e entregadores antes da primeira demo.
      </p>
    </>
  );
}
