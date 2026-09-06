FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
WORKDIR /runner
COPY images/soup-trainer/requirements.txt /runner/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && pip check && useradd --uid 10001 --create-home runner
COPY LICENSE /runner/LICENSE
COPY app /runner/app
COPY images/soup-trainer/entrypoint.py /runner/entrypoint.py
ENV PYTHONPATH=/runner PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_HOME=/output/preparation-cache HF_DATASETS_CACHE=/output/preparation-cache/datasets
USER 10001:10001
ENTRYPOINT ["python", "/runner/entrypoint.py"]
