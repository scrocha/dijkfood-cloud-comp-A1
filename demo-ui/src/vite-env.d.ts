/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CADASTRO_URL: string;
  readonly VITE_ROTAS_URL: string;
  readonly VITE_PEDIDOS_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
