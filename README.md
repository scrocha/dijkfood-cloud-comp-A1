# dijkfood-cloud-comp-A1

## `./route_service`

Nessa pasta está o serviço responsável pelo cálculo de rotas utilizando o algoritmo A* sobre o grafo de ruas de São Paulo.

### Como Executar Localmente com Docker

Como a API depende de um arquivo de grafo (`grafo_sp.graphml`) que pode ser baixado via `setup.py`, existem duas formas de testar localmente:

#### 1. Build e Execução Direta

Se você já tem o arquivo `grafo_sp.graphml` na pasta `route_service/`:

```shell
# Build da imagem (executar na raiz do projeto)
docker build -t dijkfood-route-service -f route_service/Dockerfile .

# Execução do container (Puxando credenciais automáticas do seu arquivo ~/.aws/credentials)
docker run --name route-service -p 8000:8000 \
  -v ~/.aws:/root/.aws:ro \
  -e AWS_PROFILE=default \
  dijkfood-route-service
```

#### 2. Usando o setup.py para gerar o grafo e testar

O script `setup.py` automatiza o download do grafo e o upload para o S3, além do deploy para o ECS. Para testar apenas o Docker localmente garantindo que o grafo existe:

```shell
# 1. Gere o grafo localmente (requer dependências instaladas via uv)
uv run python route_service/setup.py

# 2. Build e Run do Docker (conforme acima)
docker build -t dijkfood-route-service -f route_service/Dockerfile .
docker run --name route-service -p 8000:8000 dijkfood-route-service
```

A API estará disponível em `http://localhost:8000/health`.

## `./database`

Nessa pasta estão os scripts responsáveis pela criação do banco de dados relacional das entidades estáticas do sistema, como usuários, restaurantes, entregadores e produtos. 

- `DDL.sql`: Script responsável pela criação do schema e das tabelas do banco de dados.
- `seed_db.py`: Script responsável pela criação dos dados iniciais do banco de dados.
- `main.py`: Script responsável por criar a API REST que faz a comunicação com o banco de dados.
- `models.py`: Script responsável por definir os modelos de dados que serão utilizados na API REST.
- `simulador_cadastro.py`: Script responsável por simular o cadastro de dados no banco de dados.
- `Dockerfile`: Script responsável por criar a imagem Docker da API REST.
- `deploy.py`: Script responsável por subir toda a arquitetura na AWS, incluindo o banco de dados (RDS), o ECS (Elastic Container Service) e o ECR (Elastic Container Registry).

>[!NOTE]
>Não é criada nenhuma instância de EC2 nem para popular a base, nem para rodar o simulador de cadastro. Ambos os scripts são executados localmente e são chamados pelo `deploy.py`.

### Como Executar

É necessário mover para a raiz do repositório momentâneamente o `simulador_cadastro.py` e o `Dockerfile` para então rodar: 

```shell
uv run python database/deploy.py
```

>[!NOTE]
>Vou arrumar isso depois, mas, por enquanto, ainda é preciso mover os arquivos para a raiz do repositório para que o simulador funcione.

#### Local

```shell
# move o simulador de cadastro para a raiz do repositório
mv database/simulador_cadastro.py .

# move o Dockerfile para a raiz do repositório
mv database/Dockerfile .
```

```shell
# cria o banco de dados
docker run --name dijkfood-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=dijkfood -p 5432:5432 -d postgres:15

# cria o schema e as tabelas
Get-Content DDL.sql | docker exec -i dijkfood-db psql -U postgres -d dijkfood
```

```python
# faz a carga inicial de dados
uv run python seed_db.py
```

```shell
# conecta ao banco de dados (opcional)
docker exec -it dijkfood-db psql -U postgres -d dijkfood
```

```shell
# cria a imagem docker da api
docker build -t dijkfood-api-cadastro .  

# executa a api
docker run --name api-cadastro -p 8000:8000 -e DB_HOST="host.docker.internal" -d dijkfood-api-cadastro

# executa o simulador de cadastro
uv run python simulador_cadastro.py
```