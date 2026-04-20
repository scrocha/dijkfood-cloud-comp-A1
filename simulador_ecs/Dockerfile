# Use este Dockerfile com o contexto na RAIZ do projeto (..)
# Comando: docker build -t dijkfood-simulador -f simulador_ecs/Dockerfile .

FROM python:3.11-slim

WORKDIR /app

# Atualiza pacotes e instala dependências de sistema (para o asyncpg/psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala as dependências pip da raiz do projeto
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copia a pasta original com os scripts simuladores
COPY ./carga_unitario ./carga_unitario

# O CMD padrão roda o cenário principal. O Streamlit (boto3) poderá sobrescrever este comando no ECS se necessário.
CMD ["python", "-m", "carga_unitario", "carga_unitario/combo.json"]
