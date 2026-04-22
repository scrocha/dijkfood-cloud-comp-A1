FROM python:3.11-slim

WORKDIR /app

COPY simulador_pedidos/ /app/simulador_pedidos/

RUN pip install --no-cache-dir fastapi uvicorn httpx pydantic

ENV PYTHONPATH=/app
ENV GENERAL_API_URL=http://general-api:8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8005

CMD ["python", "simulador_pedidos/main.py"]
