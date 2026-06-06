FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY agents /app/agents

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn local_agent.integrations.google_chat_bot:app --host 0.0.0.0 --port ${PORT}"]
