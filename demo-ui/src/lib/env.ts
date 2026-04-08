/**
 * Resolução da URL base do backend com prioridade:
 *   1. alb_endpoints.json (gerado pelo deploy.py, servido via /alb_endpoints.json do public/)
 *   2. Variáveis de ambiente VITE_* (.env)
 *   3. Modo docker (base vazia, usa proxy Vite)
 *
 * Uso: chamar await initEnv() no main.tsx antes de renderizar o App.
 * As funções urlCadastro/rotas/pedidos permanecem síncronas.
 */

const BACKEND_TARGET = import.meta.env.VITE_BACKEND_TARGET ?? "docker";

let _cadastro = "";
let _rotas = "";
let _pedidos = "";
let _ready = false;

function envOr(key: string): string {
  return import.meta.env[`VITE_${key}`] ?? "";
}

export async function initEnv(): Promise<void> {
  if (_ready) return;

  if (BACKEND_TARGET === "aws") {
    // Tenta carregar do JSON servido pelo public/
    try {
      const resp = await fetch("/alb_endpoints.json", { cache: "no-cache" });
      if (resp.ok) {
        const data = (await resp.json()) as Record<string, string>;
        _cadastro = data.cadastro ?? "";
        _rotas = data.rotas ?? "";
        _pedidos = data.pedidos ?? "";
        if (_cadastro && _rotas && _pedidos) {
          _ready = true;
          return;
        }
      }
    } catch {
      // ignora, cai no fallback .env
    }

    // Fallback .env
    _cadastro = envOr("CADASTRO_URL");
    _rotas = envOr("ROTAS_URL");
    _pedidos = envOr("PEDIDOS_URL");

    console.log("Env initialized (AWS Fallback):", { _cadastro, _rotas, _pedidos });
  } else {
    console.log("Env initialized (Docker Mode)");
  }
  // docker → mantém bases vazias (proxy Vite)

  _ready = true;
}

export function urlCadastro(path: string): string {
  return `${_cadastro}/cadastro${path}`;
}

export function urlRotas(path: string): string {
  return `${_rotas}/rotas${path}`;
}

export function urlPedidos(path: string): string {
  return `${_pedidos}/pedidos${path}`;
}
