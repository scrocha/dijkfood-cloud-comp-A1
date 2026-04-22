FROM python:3.11-slim

WORKDIR /app

COPY simulador_entregadores/ /app/simulador_entregadores/

RUN pip install --no-cache-dir fastapi uvicorn httpx pydantic

ENV PYTHONPATH=/app
ENV GENERAL_API_URL=http://general-api:8000

EXPOSE 8007

CMD ["python", "simulador_entregadores/main.py"]
