# DijkFood — Computação em Nuvem

Sistema de delivery de comida baseado em cloud, desenvolvido como trabalho acadêmico da disciplina de Computação em Nuvem. A infraestrutura roda inteiramente na AWS com ECS Fargate, RDS PostgreSQL e DynamoDB.

## Pré-requisitos

- [uv](https://docs.astral.sh/uv/) instalado
- AWS CLI configurado com credenciais válidas (`aws configure`)
- Docker (para rodar localmente)

## Infraestrutura AWS

- **ECS Fargate** — 3 serviços (cadastro, rotas, pedidos) com auto scaling 2–10 tasks por serviço
- **RDS PostgreSQL 15** — banco relacional (db.t4g.small, 20 GB gp3)
- **DynamoDB** — estado dos pedidos e localização dos entregadores
- **ALB** — load balancer para os serviços e simuladores
- **ECR** — registry das imagens Docker
- **CloudWatch** — logs e métricas

## Como rodar na AWS

### Deploy completo

O script principal faz tudo em sequência: provisiona a infraestrutura, sobe os simuladores e roda o benchmark.

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

O sistema demora para ficar estável após o provisionamento. O benchmark roda 5 minutos por cenário de carga e por isso nos primeiros ~40 segundos as métricas vão estar ruins enquanto o ECS provisiona as primeiras tasks e o auto scaling ainda não reagiu. Os resultados válidos são os coletados após esse aquecimento.

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

## Autores

Gustavo Tironi, Kauan Mariani Ferreira, Matheus Fillype Ferreira de Carvalho, Sillas Rocha da Costa
