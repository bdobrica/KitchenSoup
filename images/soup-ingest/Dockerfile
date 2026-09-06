FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
WORKDIR /runner
COPY images/soup-ingest/requirements.txt /runner/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home runner
COPY images/soup-ingest/runner.py /runner/runner.py
USER 10001:10001
ENTRYPOINT ["python", "/runner/runner.py"]
