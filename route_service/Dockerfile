FROM python:3.11-slim

# 1. Variáveis de ambiente para Python e UV
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

# 2. Instalação do uv de forma eficiente
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# 3. Instalação de dependências do sistema com limpeza agressiva
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgdal-dev \
    libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

# 4. Cache de dependências (Aproveitando melhor o UV)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project --frozen

# 5. Copiar o código e instalar o projeto
COPY route_service/ /app/route_service/

RUN uv run route_service/download_graph.py && rm -rf /tmp/* \
    && rm -f /app/route_service/grafo_sp.graphml

EXPOSE 8001

CMD ["uvicorn", "route_service.api:app", "--host", "0.0.0.0", "--port", "8001"]