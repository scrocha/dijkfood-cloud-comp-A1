import { Link, Outlet } from "react-router-dom";

export function Layout() {
  return (
    <div className="layout">
      <nav>
        <Link to="/">Entrar</Link>
        <Link to="/app">Pedido</Link>
        <Link to="/admin">Admin</Link>
      </nav>
      <Outlet />
    </div>
  );
}
