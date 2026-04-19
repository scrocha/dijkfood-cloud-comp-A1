## Simuladores ECS — DijkFood

Este módulo gerencia o deploy, controle e monitoramento dos **simuladores de carga** do DijkFood em um cluster ECS Fargate dedicado, isolado das APIs de produção.

### Arquitetura

```
┌──────────────────────────────────────────────────┐
│           Dashboard (Streamlit local)            │
│    - Escala services via boto3                   │
│    - Controla rate do sim-pedidos                │
│    - Visualiza logs CloudWatch separados         │
└──────────────────┬───────────────────────────────┘
                   │ boto3 (update_service / run_task / logs)
                   ▼
┌──────────────────────────────────────────────────┐
│    ECS Cluster: dijkfood-simulators-cluster      │
│                                                  │
│    ALB Interno (dijkfood-sim-alb)                │
│    ┌─────────────────────────────────────────┐   │
│    │/simulador/restaurante* → sim-restaurante│   │
│    │/simulador/entregador*  → sim-entregador │   │
│    │/simulador/cliente*     → sim-pedidos    │   │
│    │(default)               → general-api    │   │
│    └─────────────────────────────────────────┘   │
│                                                  │
│    -> general-api      (Service  porta 8000)     │
│    -> sim-pedidos      (Service, porta 8005)     │
│    -> sim-restaurante  (Service, porta 8006)     │
│    -> sim-entregadores (Service, porta 8007)     │
│    -> sim-completo     (Task batch, opcional)    │
│    -> sim-carga        (Task batch, opcional)    │
│                                                  │
│    SG: dijkfood-sg-simulators                    │
└──────────────────┬───────────────────────────────┘
                   │ HTTP via ALB Principal (porta 80)
                   ▼
┌──────────────────────────────────────────────────┐
│    ECS Cluster: dijkfood-cluster (APIs)          │
│                                                  │
│    -> dijkfood-cadastro-svc  (porta 8000)        │
│    -> dijkfood-rotas-svc     (porta 8001)        │
│    -> dijkfood-pedidos-svc   (porta 8002)        │
│                                                  │
│    SG: dijkfood-sg-unified                       │
└──────────────────────────────────────────────────┘
```

### Simuladores Disponíveis

#### Services (rodam continuamente)

| Simulador | Pasta | Porta | Papel |
|---|---|---|---|
| **API Geral (Gateway)** | `general_api/` | 8000 | Orquestra checkout: chama restaurante, entregador, rotas |
| **Sim Clientes** | `simulador_pedidos/` | 8005 | Gera pedidos `POST /checkout` com rate controlável |
| **Sim Restaurante** | `simulador_restaurante/` | 8006 | Recebe `POST /prepare`, simula preparo, notifica webhook |
| **Sim Entregadores** | `simulador_entregadores/` | 8007 | Recebe rota, reporta GPS, faz pickup e delivery |

### Tasks Batch (opcionais)

| Simulador | Pasta | Descrição |
|---|---|---|
| **Sim Completo** | `simulador/` | Popula dados + dispara pedidos + ciclo completo |


### Pré-requisitos

1. Credenciais AWS válidas configuradas (`~/.aws/credentials`)
2. Docker Desktop rodando
3. Dependências instaladas (`uv sync` ou `pip install -e .`)
4. O deploy principal já foi executado (`python deploy.py`) — isso gera o `deploy_output.json`

### Fluxo de Uso

#### 1. Deploy das APIs

```bash
uv run python deploy.py  
```

Isso cria o RDS, DynamoDB, ECS cluster das APIs, ALB, e salva o `deploy_output.json` com:
- `API_URL`: endereço do ALB principal
- `SG_ID`, `VPC_ID`, `SUBNET_IDS`: dados de rede

#### 2. Deploy dos Simuladores

```bash
uv run python simulador_ecs/deploy_simulador.py
```

O script:
- Lê `deploy_output.json` automaticamente
- Cria o cluster `dijkfood-simulators-cluster`
- Cria um ALB interno para comunicação entre os 4 services
- Para cada simulador: cria ECR, builda imagem Docker, registra Task Definition
- Cria ECS Services para os 4 serviços (sim_pedidos inicia com `desiredCount=0`)
- Configura Security Groups para comunicação cross-cluster
- Salva `simulador_output.json`

#### 3. Dashboard de Controle

```bash
uv run streamlit run simulador_ecs/dashboard_carga.py
```

O dashboard permite:

- **Aba Controle**: Escalar cada service individualmente + controlar rate do sim_pedidos
- **Aba Logs**: Visualizar logs CloudWatch separados por simulador
- **Aba Status**: Ver tasks em execução, status de cada service, configuração de rede

#### 4. Destruição

```bash
# Remove tudo (APIs + simuladores, modo soft: preserva RDS e ECR)
uv run python destroy.py

# Remove TUDO incluindo RDS, ECR, Security Groups
uv run python destroy.py --hard
```

Ou para destruir apenas os simuladores:
```bash
uv run python simulador_ecs/deploy_simulador.py --destroy
```

### Comunicação Inter-Serviço

Todos os services se comunicam via o **ALB interno** (`dijkfood-sim-alb`):

| Origem | Destino | Como |
|---|---|---|
| sim_pedidos | general_api | `GENERAL_API_URL` → ALB interno (default route) |
| general_api | sim_restaurante | `SIM_RESTAURANT_URL` → ALB interno → `/simulador/restaurante*` |
| general_api | sim_entregadores | `SIM_COURIER_URL` → ALB interno → `/simulador/entregador*` |
| sim_restaurante | general_api | `GENERAL_API_URL` → ALB interno → `/webhook/restaurant-ready` |
| sim_entregadores | general_api | `GENERAL_API_URL` → ALB interno → `/webhook/courier-picked-up` |
| general_api | APIs (cadastro, rotas, pedidos) | `DATABASE_SERVICE_URL` etc → ALB principal |

### Config (`config.json`)

O arquivo `config.json` define todos os simuladores com:
- `REPO_NAME`: repositório ECR
- `TASK_FAMILY`: nome da task definition
- `TYPE`: `"service"` (persistente com ALB) ou `"task"` (batch)
- `TG_NAME`: nome do Target Group no ALB interno
- `ALB_PRIORITY` / `ALB_PATH_PATTERNS`: routing rules do ALB
- `ENV_MAPPING`: variáveis de ambiente com `{API_URL}` e `{SIM_ALB_URL}` como placeholders
- `DESIRED_COUNT`: instâncias iniciais (0 para sim_pedidos = controlável via dashboard)
- `DOCKERFILE`: caminho relativo à raiz do projeto

### Logs

Todos os logs vão para o CloudWatch Log Group `/ecs/dijkfood-simuladores`, com prefixos separados:
- `sim-gateway/*` — API Geral (gateway)
- `sim-clientes/*` — Simulador de clientes/pedidos
- `sim-restaurante/*` — Simulador de restaurante
- `sim-entregadores/*` — Simulador de entregadores
- `sim-completo/*` — Simulador completo (batch)
- `sim-carga/*` — Benchmark unitário (batch)

Podem ser visualizados pelo dashboard ou diretamente no Console AWS.
