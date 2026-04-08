import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthByName } from "../hooks/useAuthByName";

export function HomePage() {
  const [name, setName] = useState("");
  const { enter, loading, error, setError } = useAuthByName();
  const navigate = useNavigate();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await enter(name);
      navigate("/app");
    } catch {
      /* error já em hook */
    }
  }

  return (
    <>
      <h1>DijkFood — demo</h1>
      <p className="muted">Digite seu nome para entrar (sem senha). Se for novo, criamos o cadastro automaticamente.</p>
      <form className="card" onSubmit={onSubmit}>
        <label htmlFor="nome">Nome</label>
        <input
          id="nome"
          name="nome"
          autoComplete="nickname"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Ex.: Maria"
        />
        {error ? <p className="err">{error}</p> : null}
        <button type="submit" disabled={loading}>
          {loading ? "…" : "Entrar"}
        </button>
      </form>
    </>
  );
}
