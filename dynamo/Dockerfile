FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copiar arquivos de dependências
COPY pyproject.toml uv.lock ./

# Instalar dependências
RUN uv sync --no-dev --no-install-project --frozen

# Copiar o código (agora na pasta dynamo)
COPY dynamo/ /app/dynamo/

EXPOSE 8000

# Comando para rodar a API usando o módulo dynamo
CMD ["uvicorn", "dynamo.api:app", "--host", "0.0.0.0", "--port", "8000"]
