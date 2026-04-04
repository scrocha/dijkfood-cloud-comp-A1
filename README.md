# DijkFood - Sistema de Logística de Entrega Cloud Native

Este projeto é uma plataforma de entrega de comida ("DijkFood") projetada para alta escalabilidade na AWS, utilizando uma arquitetura de microserviços, bancos de dados poliglotas (Relacional e NoSQL) e computação serverless com ECS Fargate.

## Arquitetura e Serviços

O sistema é dividido em três serviços principais:

1.  **`database_service` (PostgreSQL/RDS):** Gerencia as entidades estáticas (Usuários, Restaurantes, Entregadores, Produtos) e armazena o histórico consolidado de pedidos finalizados.
2.  **`route_service` (FastAPI):** Calcula rotas otimizadas utilizando o algoritmo A* sobre o grafo de ruas de São Paulo (OSMNX).
3.  **`dynamo` (DynamoDB):** Gerencia o ciclo de vida dos pedidos em tempo real. Possui lógica de:
    *   **Máquina de Estados:** Transições rígidas de status (`CONFIRMED` -> `DELIVERED`).
    *   **TTL (Time to Live):** Pedidos expiram do DynamoDB 48h após a entrega para manter a tabela leve.
    *   **Sincronização Automática:** Ao atingir o status `DELIVERED`, o serviço condensa o histórico de tempos e envia para a tabela `PEDIDOS` no PostgreSQL.

---

## Como Executar na Nuvem (AWS)

O projeto possui um script de deploy unificado que provisiona toda a infraestrutura (RDS, DynamoDB, ECR, ALB, ECS, Auto Scaling) em uma única execução.

### 1. Deploy Unificado
A partir da raiz do repositório, execute:

```bash
uv run python deploy.py
```
*Este script retornará a URL do Load Balancer (ALB) ao final.*

### 2. Teste de Infraestrutura e Fluxo
Após o deploy, você pode validar se todos os serviços estão comunicando-se corretamente e se o fluxo de pedidos (Dynamo -> Postgres) está funcionando:

```bash
uv run python aleat/test_infra.py <URL_DO_ALB>
```
*O teste simula a criação de um pedido, atribuição de entregador e todas as mudanças de status até a entrega.*

### 3. Limpeza de Recursos
Para evitar custos desnecessários, utilize o script de destruição:

*   **Modo Soft (Padrão):** Remove containers, Load Balancer e logs, mas **mantém** o RDS e o ECR (limpando apenas os dados das tabelas) para que o próximo deploy seja instantâneo.
    ```bash
    uv run python destroy.py
    ```

*   **Modo Hard:** Remove **absolutamente tudo**, incluindo a instância do banco de dados RDS e os repositórios de imagem.
    ```bash
    uv run python destroy.py --hard
    ```

---

## Como Executar Localmente (Docker Compose)

Para desenvolvimento rápido, você pode subir toda a infraestrutura localmente:

```bash
docker-compose up --build
```

Os serviços estarão disponíveis em:
- **Cadastro (SQL):** `http://localhost:8002`
- **Rotas:** `http://localhost:8003`
- **Pedidos (Dynamo):** `http://localhost:8004`
- **DynamoDB Admin:** `http://localhost:8001` (Interface visual para o DynamoLocal)

---

## Estrutura do Projeto

- `/database`: DDL, API de cadastro e simuladores para PostgreSQL.
- `/dynamo`: Lógica do serviço de pedidos e integração com DynamoDB.
- `/route_service`: Motor de cálculo de rotas e processamento de grafos.
- `deploy.py`: Orquestrador de infraestrutura AWS (Boto3).
- `aleat/test_infra.py`: Script de validação funcional e de conectividade.
- `destroy.py`: Gerenciador de limpeza de ambiente.
