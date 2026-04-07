# DijkFood — interface demo (React)

SPA mínima para demonstrar cadastro por nome, um pedido com simulação de status e painel admin parcial.

## Pré-requisitos

- Node.js 18+
- Backend via Docker Compose: `database-service` (8002), `route-service` (8003), `order-service` (8004), Postgres e DynamoDB local.

## Desenvolvimento

```bash
cd demo-ui
npm install
npm run dev
```

Abrir http://localhost:5173 — o Vite faz proxy de `/cadastro`, `/rotas` e `/pedidos` para as portas locais (sem CORS).

Copie `.env.example` para `.env` e use **`VITE_BACKEND_TARGET`**:

- `docker` (padrão): pedidos relativos; só precisa do Compose nas portas 8002–8004.
- `aws`: defina também `VITE_CADASTRO_URL`, `VITE_ROTAS_URL` e `VITE_PEDIDOS_URL` (origem sem barra final).

## Identificação por nome

O nome digitado é convertido em `user_id` (slug). Dois nomes que geram o mesmo slug partilham a conta (aceitável para demo).

## Build

```bash
npm run build
npm run preview
```

No build, use `VITE_BACKEND_TARGET=aws` e as três `VITE_*_URL`, ou sirva `dist/` no mesmo host que o ALB com `VITE_BACKEND_TARGET=docker` e caminhos relativos.
