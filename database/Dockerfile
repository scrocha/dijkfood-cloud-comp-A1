# versão leve do python
FROM python:3.11-slim 

ENV PYTHONUNBUFFERED=1 \
	PYTHONDONTWRITEBYTECODE=1 \
	UV_PROJECT_ENVIRONMENT=/usr/local

# copia o binário do 'uv' direto da imagem oficial
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# define a pasta de trabalho na raiz do container
WORKDIR /app

# instala as dependências do sistema operacional
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# copia os arquivos de configuração do uv
COPY pyproject.toml uv.lock ./

# instala as dependências do Python
RUN uv sync --no-dev --no-install-project --frozen

# copia TODO o código da aplicação mantendo as pastas originais
COPY . .

# expõe a porta
EXPOSE 8000

# comando para iniciar a API no Uvicorn
CMD ["uvicorn", "database.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]