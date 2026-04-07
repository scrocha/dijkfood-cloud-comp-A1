import { useCallback, useEffect, useState } from "react";
import { useAdminCadastro } from "../hooks/useAdminCadastro";
import { useAdminPedidos } from "../hooks/useAdminPedidos";
import { useEntregadores } from "../hooks/useEntregadores";
import { useRestaurants } from "../hooks/useRestaurants";
import { useUsuarios } from "../hooks/useUsuarios";
import type { OrderStatus } from "../api/pedidos";
import type { EntregadorDTO, RestauranteDTO, UsuarioDTO } from "../api/cadastro";

const STATUSES: OrderStatus[] = [
  "CONFIRMED",
  "PREPARING",
  "READY_FOR_PICKUP",
  "PICKED_UP",
  "IN_TRANSIT",
  "DELIVERED",
];

type AdminSection = "restaurantes" | "entregadores" | "usuarios" | "produtos" | "pedidos";
type CadastroMode = "ver" | "criar";
type PedidosQuery = "cliente" | "status";

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: "0.85rem", marginTop: "0.75rem" }}>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function AdminPage() {
  const [section, setSection] = useState<AdminSection>("restaurantes");
  const [cadastroMode, setCadastroMode] = useState<CadastroMode>("ver");

  const { list: restaurants, reload: reloadR } = useRestaurants();
  const { list: entregadores, reload: reloadE } = useEntregadores();
  const { list: usuarios, reload: reloadU, loading: listUsersLoading, error: listUsersErr } = useUsuarios();
  const { addUsuario, addRestaurante, addEntregador, addProduto, getUsuario, getRestaurante, getEntregador } =
    useAdminCadastro();
  const { byCustomer, byStatus, loading, error, loadCustomer, loadStatus, setError, clearPedidosResults } =
    useAdminPedidos();

  const [selectedRestId, setSelectedRestId] = useState("");
  const [restDetail, setRestDetail] = useState<RestauranteDTO | null>(null);
  const [restDetailErr, setRestDetailErr] = useState<string | null>(null);
  const [restLoading, setRestLoading] = useState(false);

  const [selectedEntId, setSelectedEntId] = useState("");
  const [entDetail, setEntDetail] = useState<EntregadorDTO | null>(null);
  const [entDetailErr, setEntDetailErr] = useState<string | null>(null);
  const [entLoading, setEntLoading] = useState(false);

  const [selectedUserId, setSelectedUserId] = useState("");
  const [otherUserId, setOtherUserId] = useState("");
  const [userDetail, setUserDetail] = useState<UsuarioDTO | null>(null);
  const [userDetailErr, setUserDetailErr] = useState<string | null>(null);
  const [userLoading, setUserLoading] = useState(false);

  const [uMsg, setUMsg] = useState<string | null>(null);
  const [rMsg, setRMsg] = useState<string | null>(null);
  const [eMsg, setEMsg] = useState<string | null>(null);
  const [pMsg, setPMsg] = useState<string | null>(null);

  const [prodRestId, setProdRestId] = useState("");

  const [pedidosQuery, setPedidosQuery] = useState<PedidosQuery>("cliente");
  const [customerQ, setCustomerQ] = useState("");
  const [statusQ, setStatusQ] = useState<OrderStatus>("CONFIRMED");

  useEffect(() => {
    if (restaurants.length > 0 && !prodRestId) {
      setProdRestId(restaurants[0].rest_id);
    }
  }, [restaurants, prodRestId]);

  useEffect(() => {
    if (section !== "restaurantes" || cadastroMode !== "ver" || !selectedRestId) {
      setRestDetail(null);
      setRestDetailErr(null);
      setRestLoading(false);
      return;
    }
    let cancelled = false;
    setRestLoading(true);
    setRestDetailErr(null);
    void getRestaurante(selectedRestId)
      .then((r) => {
        if (!cancelled) setRestDetail(r);
      })
      .catch((err) => {
        if (!cancelled) setRestDetailErr(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setRestLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [section, cadastroMode, selectedRestId, getRestaurante]);

  useEffect(() => {
    if (section !== "entregadores" || cadastroMode !== "ver" || !selectedEntId) {
      setEntDetail(null);
      setEntDetailErr(null);
      setEntLoading(false);
      return;
    }
    let cancelled = false;
    setEntLoading(true);
    setEntDetailErr(null);
    void getEntregador(selectedEntId)
      .then((r) => {
        if (!cancelled) setEntDetail(r);
      })
      .catch((err) => {
        if (!cancelled) setEntDetailErr(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setEntLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [section, cadastroMode, selectedEntId, getEntregador]);

  const fetchUser = useCallback(
    async (rawId: string) => {
      const trimmed = rawId.trim();
      if (!trimmed) return;
      setUserLoading(true);
      setUserDetailErr(null);
      setUserDetail(null);
      try {
        const u = await getUsuario(trimmed);
        if (u) {
          setUserDetail(u);
        } else {
          setUserDetailErr("Não encontrado (404).");
        }
      } catch (err) {
        setUserDetailErr(err instanceof Error ? err.message : String(err));
      } finally {
        setUserLoading(false);
      }
    },
    [getUsuario]
  );

  useEffect(() => {
    if (section !== "usuarios" || cadastroMode !== "ver") {
      setUserDetail(null);
      setUserDetailErr(null);
      setUserLoading(false);
      return;
    }
    if (!selectedUserId) {
      setUserDetail(null);
      setUserDetailErr(null);
      return;
    }
    let cancelled = false;
    setUserLoading(true);
    setUserDetailErr(null);
    setUserDetail(null);
    void getUsuario(selectedUserId)
      .then((u) => {
        if (cancelled) return;
        if (u) {
          setUserDetail(u);
        } else {
          setUserDetailErr("Não encontrado (404).");
        }
      })
      .catch((err) => {
        if (!cancelled) setUserDetailErr(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setUserLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [section, cadastroMode, selectedUserId, getUsuario]);

  async function submitUsuario(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setUMsg(null);
    const fd = new FormData(e.currentTarget);
    const user_id = String(fd.get("user_id") ?? "");
    try {
      await addUsuario({
        user_id,
        primeiro_nome: String(fd.get("primeiro_nome") ?? ""),
        ultimo_nome: String(fd.get("ultimo_nome") ?? ""),
        email: String(fd.get("email") ?? ""),
        telefone: String(fd.get("telefone") ?? ""),
        endereco_latitude: Number(fd.get("lat")),
        endereco_longitude: Number(fd.get("lon")),
      });
      void reloadU();
      setUMsg("Usuario criado.");
      e.currentTarget.reset();
    } catch (err) {
      setUMsg(err instanceof Error ? err.message : String(err));
    }
  }

  async function submitRest(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setRMsg(null);
    const fd = new FormData(e.currentTarget);
    try {
      await addRestaurante({
        rest_id: String(fd.get("rest_id") ?? ""),
        nome: String(fd.get("nome") ?? ""),
        tipo_cozinha: String(fd.get("tipo_cozinha") ?? ""),
        endereco_latitude: Number(fd.get("lat")),
        endereco_longitude: Number(fd.get("lon")),
      });
      setRMsg("Restaurante criado.");
      e.currentTarget.reset();
      void reloadR();
    } catch (err) {
      setRMsg(err instanceof Error ? err.message : String(err));
    }
  }

  async function submitEnt(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setEMsg(null);
    const fd = new FormData(e.currentTarget);
    try {
      await addEntregador({
        entregador_id: String(fd.get("entregador_id") ?? ""),
        nome: String(fd.get("nome") ?? ""),
        tipo_veiculo: String(fd.get("tipo_veiculo") ?? ""),
        endereco_latitude: Number(fd.get("lat")),
        endereco_longitude: Number(fd.get("lon")),
      });
      setEMsg("Entregador criado.");
      e.currentTarget.reset();
      void reloadE();
    } catch (err) {
      setEMsg(err instanceof Error ? err.message : String(err));
    }
  }

  async function submitProd(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPMsg(null);
    const fd = new FormData(e.currentTarget);
    try {
      await addProduto({
        prod_id: String(fd.get("prod_id") ?? ""),
        nome: String(fd.get("nome") ?? ""),
        rest_id: prodRestId || String(fd.get("rest_id") ?? ""),
      });
      setPMsg("Produto criado.");
      e.currentTarget.reset();
    } catch (err) {
      setPMsg(err instanceof Error ? err.message : String(err));
    }
  }

  const isPedidos = section === "pedidos";
  const showProdutos = section === "produtos";

  return (
    <>
      <h1>Admin (PoC)</h1>

      <div className="card admin-nav">
        <div className="admin-nav-field">
          <label htmlFor="admin-section">Área</label>
          <select
            id="admin-section"
            value={section}
            onChange={(e) => {
              const s = e.target.value as AdminSection;
              setSection(s);
              setError(null);
            }}
          >
            <option value="restaurantes">Restaurantes</option>
            <option value="entregadores">Entregadores</option>
            <option value="usuarios">Usuarios</option>
            <option value="produtos">Produtos</option>
            <option value="pedidos">Pedidos (Dynamo)</option>
          </select>
        </div>

        {!isPedidos && !showProdutos && (
          <div className="admin-nav-field">
            <label htmlFor="admin-mode">Modo</label>
            <select
              id="admin-mode"
              value={cadastroMode}
              onChange={(e) => setCadastroMode(e.target.value as CadastroMode)}
            >
              <option value="ver">Ver registo</option>
              <option value="criar">Criar novo</option>
            </select>
          </div>
        )}
      </div>

      <p className="muted" style={{ marginTop: "-0.5rem" }}>
        Escolha a área e o modo. As listas de cadastro vêm da API (Postgres). Não há atualizar ou apagar neste
        backend.
      </p>

      {section === "restaurantes" && cadastroMode === "ver" && (
        <div className="card">
          <h2>Restaurante — ver</h2>
          <label htmlFor="pick-rest">Registo</label>
          <select
            id="pick-rest"
            value={selectedRestId}
            onChange={(e) => setSelectedRestId(e.target.value)}
            disabled={restaurants.length === 0}
          >
            <option value="">— selecione —</option>
            {restaurants.map((r) => (
              <option key={r.rest_id} value={r.rest_id}>
                {r.nome} ({r.rest_id})
              </option>
            ))}
          </select>
          {restaurants.length === 0 ? <p className="muted">Nenhum restaurante na base.</p> : null}
          {restLoading ? <p className="muted">A carregar…</p> : null}
          {restDetailErr ? <p className="err">{restDetailErr}</p> : null}
          {restDetail ? <JsonBlock value={restDetail} /> : null}
        </div>
      )}

      {section === "restaurantes" && cadastroMode === "criar" && (
        <div className="card">
          <h2>Restaurante — criar</h2>
          <form onSubmit={submitRest}>
            <label>rest_id</label>
            <input name="rest_id" required />
            <label>nome</label>
            <input name="nome" required />
            <label>tipo_cozinha</label>
            <input name="tipo_cozinha" required />
            <label>latitude</label>
            <input name="lat" type="number" step="any" defaultValue="-23.56" required />
            <label>longitude</label>
            <input name="lon" type="number" step="any" defaultValue="-46.66" required />
            <button type="submit">Criar restaurante</button>
            {rMsg ? <p className={rMsg.includes("criado") ? "muted" : "err"}>{rMsg}</p> : null}
          </form>
        </div>
      )}

      {section === "entregadores" && cadastroMode === "ver" && (
        <div className="card">
          <h2>Entregador — ver</h2>
          <label htmlFor="pick-ent">Registo</label>
          <select
            id="pick-ent"
            value={selectedEntId}
            onChange={(e) => setSelectedEntId(e.target.value)}
            disabled={entregadores.length === 0}
          >
            <option value="">— selecione —</option>
            {entregadores.map((x) => (
              <option key={x.entregador_id} value={x.entregador_id}>
                {x.nome} ({x.entregador_id})
              </option>
            ))}
          </select>
          {entregadores.length === 0 ? <p className="muted">Nenhum entregador na base.</p> : null}
          {entLoading ? <p className="muted">A carregar…</p> : null}
          {entDetailErr ? <p className="err">{entDetailErr}</p> : null}
          {entDetail ? <JsonBlock value={entDetail} /> : null}
        </div>
      )}

      {section === "entregadores" && cadastroMode === "criar" && (
        <div className="card">
          <h2>Entregador — criar</h2>
          <form onSubmit={submitEnt}>
            <label>entregador_id</label>
            <input name="entregador_id" required />
            <label>nome</label>
            <input name="nome" required />
            <label>tipo_veiculo</label>
            <input name="tipo_veiculo" defaultValue="Moto" required />
            <label>latitude</label>
            <input name="lat" type="number" step="any" defaultValue="-23.57" required />
            <label>longitude</label>
            <input name="lon" type="number" step="any" defaultValue="-46.65" required />
            <button type="submit">Criar entregador</button>
            {eMsg ? <p className={eMsg.includes("criado") ? "muted" : "err"}>{eMsg}</p> : null}
          </form>
        </div>
      )}

      {section === "usuarios" && cadastroMode === "ver" && (
        <div className="card">
          <h2>Usuario — ver</h2>
          {listUsersErr ? <p className="err">{listUsersErr}</p> : null}
          <label htmlFor="pick-user">Registo na base de dados</label>
          <select
            id="pick-user"
            value={selectedUserId}
            onChange={(e) => {
              setSelectedUserId(e.target.value);
              setOtherUserId("");
            }}
            disabled={listUsersLoading || usuarios.length === 0}
          >
            <option value="">— selecione —</option>
            {usuarios.map((u) => (
              <option key={u.user_id} value={u.user_id}>
                {u.primeiro_nome} {u.ultimo_nome} ({u.user_id})
              </option>
            ))}
          </select>
          {listUsersLoading ? <p className="muted">A carregar lista…</p> : null}
          {!listUsersLoading && usuarios.length === 0 ? (
            <p className="muted">Nenhum usuario na base. Crie um ou rode o seed de cadastro.</p>
          ) : null}
          <button type="button" className="secondary" style={{ marginTop: "0.5rem" }} onClick={() => void reloadU()}>
            Recarregar lista
          </button>

          <form
            style={{ marginTop: "1rem" }}
            onSubmit={(e) => {
              e.preventDefault();
              setSelectedUserId("");
              void fetchUser(otherUserId);
            }}
          >
            <label htmlFor="other-user-id">Outro user_id</label>
            <input
              id="other-user-id"
              value={otherUserId}
              onChange={(e) => setOtherUserId(e.target.value)}
              placeholder="slug ou uuid"
            />
            <button type="submit" className="secondary" disabled={userLoading}>
              Buscar na API
            </button>
          </form>

          {userLoading ? <p className="muted">A carregar…</p> : null}
          {userDetailErr ? <p className="err">{userDetailErr}</p> : null}
          {userDetail ? <JsonBlock value={userDetail} /> : null}
        </div>
      )}

      {section === "usuarios" && cadastroMode === "criar" && (
        <div className="card">
          <h2>Usuario — criar</h2>
          <form onSubmit={submitUsuario}>
            <label>user_id</label>
            <input name="user_id" required />
            <label>primeiro_nome</label>
            <input name="primeiro_nome" required />
            <label>ultimo_nome</label>
            <input name="ultimo_nome" required />
            <label>email</label>
            <input name="email" type="email" required />
            <label>telefone</label>
            <input name="telefone" required />
            <label>latitude</label>
            <input name="lat" type="number" step="any" defaultValue="-23.56" required />
            <label>longitude</label>
            <input name="lon" type="number" step="any" defaultValue="-46.66" required />
            <button type="submit">Criar usuario</button>
            {uMsg ? <p className={uMsg.includes("criado") ? "muted" : "err"}>{uMsg}</p> : null}
          </form>
        </div>
      )}

      {showProdutos && (
        <div className="card">
          <h2>Produto — criar</h2>
          <p className="muted">A API não expõe listagem de produtos. Escolha o restaurante (FK).</p>
          <form onSubmit={submitProd}>
            <label>prod_id</label>
            <input name="prod_id" required />
            <label>nome</label>
            <input name="nome" required />
            <label>rest_id</label>
            <select
              value={prodRestId}
              onChange={(e) => setProdRestId(e.target.value)}
              disabled={restaurants.length === 0}
              required
            >
              {restaurants.length === 0 ? (
                <option value="">— sem restaurantes —</option>
              ) : (
                restaurants.map((r) => (
                  <option key={r.rest_id} value={r.rest_id}>
                    {r.nome} ({r.rest_id})
                  </option>
                ))
              )}
            </select>
            <button type="submit" disabled={restaurants.length === 0}>
              Criar produto
            </button>
            {pMsg ? <p className={pMsg.includes("criado") ? "muted" : "err"}>{pMsg}</p> : null}
          </form>
        </div>
      )}

      {isPedidos && (
        <div className="card">
          <h2>Pedidos (Dynamo)</h2>

          <label htmlFor="pedidos-q">Consulta</label>
          <select
            id="pedidos-q"
            value={pedidosQuery}
            onChange={(e) => {
              setPedidosQuery(e.target.value as PedidosQuery);
              clearPedidosResults();
            }}
          >
            <option value="cliente">Por cliente (customer_id)</option>
            <option value="status">Por status</option>
          </select>

          {pedidosQuery === "cliente" && (
            <form
              style={{ marginTop: "0.75rem" }}
              onSubmit={(e) => {
                e.preventDefault();
                setError(null);
                void loadCustomer(customerQ.trim());
              }}
            >
              <label htmlFor="cust-q">customer_id</label>
              <input id="cust-q" value={customerQ} onChange={(e) => setCustomerQ(e.target.value)} />
              <button type="submit" disabled={loading}>
                Listar
              </button>
            </form>
          )}

          {pedidosQuery === "status" && (
            <form
              style={{ marginTop: "0.75rem" }}
              onSubmit={(e) => {
                e.preventDefault();
                setError(null);
                void loadStatus(statusQ);
              }}
            >
              <label htmlFor="status-q">status</label>
              <select id="status-q" value={statusQ} onChange={(e) => setStatusQ(e.target.value as OrderStatus)}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <button type="submit" disabled={loading}>
                Listar
              </button>
            </form>
          )}

          {error ? <p className="err">{error}</p> : null}
          {pedidosQuery === "cliente" && byCustomer !== null ? <JsonBlock value={byCustomer} /> : null}
          {pedidosQuery === "status" && byStatus !== null ? <JsonBlock value={byStatus} /> : null}
        </div>
      )}
    </>
  );
}
