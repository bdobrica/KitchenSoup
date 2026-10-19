# vLLM 0.10.2, linux/amd64. V0 + XFormers retains Turing (SM75) support.
FROM vllm/vllm-openai:v0.10.2@sha256:df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    HF_HOME=/tmp/hf \
    HOME=/tmp \
    USER=runner \
    LOGNAME=runner \
    TORCHINDUCTOR_CACHE_DIR=/tmp/torchinductor \
    TRITON_CACHE_DIR=/tmp/triton \
    VLLM_CACHE_ROOT=/tmp/vllm \
    XDG_CACHE_HOME=/tmp/cache \
    CUDA_CACHE_PATH=/tmp/cuda \
    OMP_NUM_THREADS=4 \
    TOKENIZERS_PARALLELISM=false \
    VLLM_USE_V1=0 \
    VLLM_ATTENTION_BACKEND=XFORMERS \
    VLLM_NO_USAGE_STATS=1 \
    DO_NOT_TRACK=1
USER 10001:10001
ENTRYPOINT ["python3", "-m", "vllm.entrypoints.openai.api_server"]
