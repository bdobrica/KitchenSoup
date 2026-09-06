FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /opt/kitchensoup
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install . && useradd --uid 10001 --create-home kitchensoup
USER kitchensoup
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
