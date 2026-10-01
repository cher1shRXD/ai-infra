FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY context_api ./context_api
COPY migrations ./migrations

EXPOSE 8000
CMD ["uvicorn", "context_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
