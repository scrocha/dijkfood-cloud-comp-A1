# DijkFood Demo UI

Interface de demonstração do sistema DijkFood. Permite fazer pedidos, acompanhar status e simular o fluxo completo de entrega.

## Pré-requisitos

- Node.js 18+
- npm

## Instalação

```bash
cd demo-ui
npm install
```

## Configuração do backend

Edite (ou crie) o arquivo `demo-ui/.env`:

### Opção 1 — AWS (ALB)

```env
VITE_BACKEND_TARGET=aws
VITE_CADASTRO_URL=http://<seu-alb>.us-east-1.elb.amazonaws.com
VITE_ROTAS_URL=http://<seu-alb>.us-east-1.elb.amazonaws.com
VITE_PEDIDOS_URL=http://<seu-alb>.us-east-1.elb.amazonaws.com
```

### Opção 2 — Docker local

```env
VITE_BACKEND_TARGET=docker
```

O Vite faz proxy automático para `8002` (cadastro), `8003` (rotas) e `8004` (pedidos). Não é preciso preencher as `VITE_*_URL`.

## Rodando

```bash
npm run dev
```

Acesse [http://localhost:5173](http://localhost:5173). Após mudar o `.env`, reinicie o `npm run dev`.

## Seed de dados (AWS)

Antes da primeira demo na AWS, cadastre os dados iniciais a partir da raiz do projeto:

```bash
python seed_aws_demo.py
```

Isso cria 3 usuários, 3 restaurantes e 3 entregadores. Os IDs de usuário para logar no front são:
`demo-usuario-1`, `demo-usuario-2`, `demo-usuario-3`.

## Fluxo da demo

1. **Home** — informe seu nome para entrar (deve existir no cadastro)
2. **Fazer pedido** — escolha restaurante e item, clique em "Fazer pedido"
3. **Acompanhamento** — status e localização do entregador atualizam automaticamente
4. **Admin** — cadastre novos restaurantes e entregadores antes da demo
