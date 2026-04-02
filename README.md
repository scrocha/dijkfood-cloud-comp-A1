# dijkfood-cloud-comp-A1

## `./database`

Nessa pasta estão os scripts responsáveis pela criação do banco de dados relacional das entidades estáticas do sistema, como usuários, restaurantes, entregadores e produtos. 

- `DDL.sql`: Script responsável pela criação do schema e das tabelas do banco de dados.
- `seed_db.py`: Script responsável pela criação dos dados iniciais do banco de dados.
- `main.py`: Script responsável por criar a API REST (FastAPI). Utiliza pool de conexões assíncronas (`asyncpg`) e possui rotas otimizadas (`/batch`) para suportar alta volumetria de inserções simultâneas.
- `models.py`: Script responsável por definir os modelos de dados (Pydantic) que serão utilizados na API REST.
- `simulador_cadastro.py`: Script assíncrono responsável por simular o tráfego de usuários. Utiliza a estratégia de envio em lote (Batching) para atingir alto *throughput* (+200 req/s) mantendo a latência baixa.
- `Dockerfile`: Script responsável por criar a imagem Docker otimizada da API REST.
- `deploy.py`: Script automatizado (`boto3`) que provisiona toda a arquitetura na AWS. Ele configura a rede (Security Groups), o banco de dados RDS (otimizado com discos `gp3`), o ECR, o Application Load Balancer (ALB) e o ECS Fargate com **Application Auto Scaling** já configurado.

> [!NOTE]
> Não é criada nenhuma instância de EC2 nem para popular a base, nem para rodar o simulador de carga. Ambos os scripts são executados localmente e comunicam-se de forma assíncrona com os recursos criados pelo `deploy.py`.

### Como Executar na Nuvem (AWS)

Para provisionar a infraestrutura completa na AWS, fazer o build da imagem, deploy da API e rodar o teste de carga automaticamente, execute a partir da raiz do repositório:

```shell
uv run python database/deploy.py
```

### Como Executar Localmente

Para rodar a aplicação inteira no seu ambiente local (via Docker) a partir da raiz do repositório, siga os passos abaixo:

```shell
# 1. Cria e roda o banco de dados PostgreSQL
docker run --name dijkfood-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=dijkfood -p 5432:5432 -d postgres:15

# 2. Cria o schema e as tabelas (Se estiver no Windows/PowerShell)
Get-Content database/DDL.sql | docker exec -i dijkfood-db psql -U postgres -d dijkfood
# (Se estiver no Linux/Mac)
cat database/DDL.sql | docker exec -i dijkfood-db psql -U postgres -d dijkfood

# 3. Faz a carga inicial de dados fixos
uv run python database/seed_db.py

# 4. Cria a imagem docker da API (apontando para o contexto raiz)
docker build -t dijkfood-api-cadastro -f database/Dockerfile . 

# 5. Executa o container da API apontando para o banco local
docker run --name api-cadastro -p 8000:8000 -e DB_HOST="host.docker.internal" -d dijkfood-api-cadastro

# 6. Executa o simulador de carga para testar a performance
uv run python database/simulador_cadastro.py

# Utilidade: Conecta ao banco de dados interativamente (opcional)
docker exec -it dijkfood-db psql -U postgres -d dijkfood
```