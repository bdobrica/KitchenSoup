FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS builder
ENV PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install --prefix=/install .

FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
ARG VERSION=0.1.0
ARG REVISION=unknown
LABEL org.opencontainers.image.title="KitchenSoup" \
    org.opencontainers.image.description="Lightweight language-model fine-tuning control plane" \
    org.opencontainers.image.source="https://github.com/bdobrica/KitchenSoup" \
    org.opencontainers.image.licenses="Apache-2.0" \
    org.opencontainers.image.version=$VERSION \
    org.opencontainers.image.revision=$REVISION
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /opt/kitchensoup
COPY --from=builder /install /usr/local
COPY alembic.ini ./
COPY alembic ./alembic
RUN useradd --uid 10001 --create-home kitchensoup
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
