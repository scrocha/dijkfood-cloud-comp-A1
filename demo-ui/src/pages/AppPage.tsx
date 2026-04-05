import { useMemo, useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { MOCK_PRODUCTS } from "../config/mockProducts";
import { POLL_DRIVER_MS, POLL_ORDER_MS } from "../config/demoTiming";
import { useDriverLocationPolling } from "../hooks/useDriverLocationPolling";
import { useEntregadores } from "../hooks/useEntregadores";
import { useOrderPolling } from "../hooks/useOrderPolling";
import { useRestaurants } from "../hooks/useRestaurants";
import { useRunOrderDemo } from "../hooks/useRunOrderDemo";
import { readSession, clearSession, type DemoSession } from "../session/demoSession";

function formatLoc(loc: { lat: string; lng: string; updated_at?: string } | null): string {
  if (!loc) return "—";
  return `lat ${loc.lat}, lng ${loc.lng}${loc.updated_at ? ` · ${loc.updated_at}` : ""}`;
}

export function AppPage() {
  /** Sincronizar com sessionStorage na 1ª renderização; useEffect chega tarde demais para Navigate. */
  const [session, setSession] = useState<DemoSession | null>(() => readSession());
  const [restId, setRestId] = useState("");
  const [productIdx, setProductIdx] = useState(0);
  const [orderId, setOrderId] = useState<string | null>(null);

  const { list: restaurants, loading: lr, error: er } = useRestaurants();
  const { first: entregador, loading: le, error: ee } = useEntregadores();
  const { runDemo, phase, busy, error: simErr, setError: setSimErr, cancel } = useRunOrderDemo();

  const { order, error: pollErr } = useOrderPolling(orderId, POLL_ORDER_MS, !!orderId);
  const { loc: driverLoc } = useDriverLocationPolling(
    entregador?.entregador_id ?? null,
    POLL_DRIVER_MS,
    !!orderId && !!entregador
  );

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

    await runDemo({
      customerId: sess.userId,
      restaurantId: restaurant.rest_id,
      items,
      totalValue: mock.unitPrice,
      restaurant: { lat: restaurant.endereco_latitude, lon: restaurant.endereco_longitude },
      customer: { lat: sess.lat, lon: sess.lng },
      entregadorId: entregador.entregador_id,
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
        <p>
          <span className="muted">Pedido</span>{" "}
          <span className="mono">{orderId ?? "—"}</span>
        </p>
        <p>
          <span className="muted">Status (API)</span>{" "}
          <strong>{order?.status ?? "—"}</strong>
        </p>
        <p>
          <span className="muted">Fase (simulação)</span>{" "}
          {phase || "—"}
        </p>
        <p>
          <span className="muted">Entregador</span>{" "}
          {entregador ? `${entregador.nome} (${entregador.entregador_id})` : "—"}
        </p>
        <p className="mono">{formatLoc(driverLoc)}</p>
      </div>

      <p className="muted">
        <Link to="/admin">Admin</Link> — cadastre restaurantes e entregadores antes da primeira demo.
      </p>
    </>
  );
}
