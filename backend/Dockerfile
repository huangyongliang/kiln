FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 kiln

COPY pyproject.toml ./
COPY kiln ./kiln

RUN pip install --no-cache-dir .

USER kiln

EXPOSE 8000

CMD ["uvicorn", "kiln.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
