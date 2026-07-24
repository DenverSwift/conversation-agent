FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

CMD ["python", "-m", "conversation_agent"]
