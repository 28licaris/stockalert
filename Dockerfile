FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml .
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir .
COPY app app
COPY .env.example ./.env
CMD ["uvicorn", "app.main_api:app", "--host", "0.0.0.0", "--port", "8000"]
