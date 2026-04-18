FROM python:3.11-slim

WORKDIR /app

COPY simulador_restaurante/ /app/simulador_restaurante/

RUN pip install --no-cache-dir fastapi uvicorn httpx pydantic

ENV PYTHONPATH=/app
ENV GENERAL_API_URL=http://general-api:8000

EXPOSE 8006

CMD ["python", "simulador_restaurante/main.py"]
