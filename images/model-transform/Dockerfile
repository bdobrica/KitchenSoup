FROM vllm/vllm-openai:v0.10.2@sha256:df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6
COPY images/model-transform/requirements.txt /runner/requirements.txt
COPY scripts/check_transform_dependencies.py /runner/check_dependencies.py
RUN python3 -m pip uninstall -y vllm && python3 -m pip install --no-cache-dir --no-deps -r /runner/requirements.txt && python3 /runner/check_dependencies.py
WORKDIR /runner
COPY app /runner/app
ENV PYTHONPATH=/runner PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_HOME=/tmp/hf HOME=/tmp USER=runner LOGNAME=runner OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false TRITON_CACHE_DIR=/tmp/triton
USER 10001:10001
ENTRYPOINT ["python3", "-m", "app.transformations.runner"]
