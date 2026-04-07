/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `docker` (default) | `aws` — ver `src/lib/env.ts` */
  readonly VITE_BACKEND_TARGET?: string;
  readonly VITE_CADASTRO_URL?: string;
  readonly VITE_ROTAS_URL?: string;
  readonly VITE_PEDIDOS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
