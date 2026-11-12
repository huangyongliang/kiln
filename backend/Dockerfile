FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --gid 10001 kiln \
    && useradd --create-home --uid 10001 --gid 10001 kiln \
    && mkdir -p /var/lib/kiln/artifacts \
    && chown -R kiln:kiln /var/lib/kiln

COPY pyproject.toml ./
COPY kiln ./kiln
COPY docker-entrypoint.py /usr/local/bin/kiln-entrypoint

RUN pip install --no-cache-dir . \
    && chmod 755 /usr/local/bin/kiln-entrypoint

EXPOSE 8000

# The entrypoint may start as root to chown KILN_ARTIFACT_ROOT, then the API
# process continues as uid 10001.
ENTRYPOINT ["python", "/usr/local/bin/kiln-entrypoint"]
CMD ["uvicorn", "kiln.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
