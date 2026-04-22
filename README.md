# DijkFood — Cloud Computing A1

Sistema de delivery de comida baseado em cloud, desenvolvido como trabalho acadêmico da disciplina de Computação em Nuvem. A infraestrutura roda inteiramente na AWS com ECS Fargate, RDS PostgreSQL e DynamoDB.

## Pré-requisitos

- [uv](https://docs.astral.sh/uv/) instalado
- AWS CLI configurado com credenciais válidas (`aws configure`)
- Docker (para rodar localmente)

## Como rodar na AWS

### Deploy completo

O script principal faz tudo em sequência: provisiona a infra, sobe os simuladores e roda o benchmark.

```bash
uv run python infra/deploy.py
```

Ao final, ele imprime a URL do ALB e as instruções para acessar o dashboard.

### Deploy em etapas (recomendado)

Você só precisa rodar o deploy uma vez. Nas próximas execuções, basta rodar só o benchmark:

```bash
# Primeira vez: provisiona tudo
uv run python infra/deploy_infra.py
uv run python infra/deploy_simuladores.py

# Próximas vezes: só roda os testes
uv run python infra/run_benchmark.py
```

### Aviso: tempo de estabilização

O sistema demora para ficar estável após o provisionamento. O benchmark roda 5 minutos por cenário de carga justamente por isso — nos primeiros ~40 segundos as métricas vão estar ruins enquanto o ECS provisiona as primeiras tasks e o auto scaling ainda não reagiu. Os resultados válidos são os coletados após esse aquecimento.

### Destruir infraestrutura

```bash
# Soft (padrão): remove containers, ALB e logs. Mantém RDS e ECR para o próximo deploy ser mais rápido.
uv run python infra/destroy.py

# Hard: remove absolutamente tudo, incluindo RDS e repositórios de imagem.
uv run python infra/destroy.py --hard
```

## Como rodar localmente

```bash
docker-compose up --build
```

Serviços disponíveis:

| Serviço | URL |
|---|---|
| Cadastro (SQL) | http://localhost:8002 |
| Rotas | http://localhost:8003 |
| Pedidos (Dynamo) | http://localhost:8004 |
| DynamoDB Admin UI | http://localhost:8001 |

## Autores

Gustavo Tironi, Kauan Mariani Ferreira, Matheus Fillype Ferreira de Carvalho, Sillas Rocha da Costa