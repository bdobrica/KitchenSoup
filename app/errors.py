"""Bounded, payload-free explanations; diagnostics never authorize lifecycle changes."""

from pydantic import BaseModel


class UserError(BaseModel):
    code: str
    message: str
    action: str


class ErrorResponse(BaseModel):
    detail: str
    error: UserError | None = None
    advanced_detail: str | None = None


def explain(detail: str, fallback: str = "operation_failed") -> UserError:
    text = detail[-262144:].lower()
    if any(
        value in text
        for value in (
            "cuda out of memory",
            "cuda error: out of memory",
            "torch.outofmemoryerror",
            "cublas_status_alloc_failed",
        )
    ):
        return UserError(
            code="gpu_memory",
            message="This workload does not fit in the available memory.",
            action="Stop other GPU workloads, choose a smaller model or a lower-memory recipe, "
            "or use a larger GPU.",
        )
    if "oomkilled" in text:
        return UserError(
            code="target_memory",
            message="The compute target ran out of system memory.",
            action="Increase the target's RAM limit, stop competing workloads "
            "or choose a smaller model.",
        )
    if any(
        value in text
        for value in (
            "gated",
            "gatedrepoerror",
            "hugging face",
            "huggingface.co",
            "hf_hub",
            "model download",
        )
    ):
        return UserError(
            code="model_download",
            message="KitchenSoup could not download the model.",
            action="Check the repository and revision, network access, "
            "and that the model is public and ungated.",
        )
    if (
        any(value in text for value in ("storage", "object store", "s3", "nosuchbucket"))
        or fallback == "storage_unavailable"
    ):
        return UserError(
            code="storage_unavailable",
            message="Artifact storage is unavailable.",
            action="Check the object-store service, bucket, credentials "
            "and available disk space, then retry.",
        )
    if (
        any(
            value in text
            for value in (
                "connect",
                "unreachable",
                "timed out",
                "timeout",
                "daemon",
                "kubeconfig",
                "ssh",
            )
        )
        or fallback == "executor_unavailable"
    ):
        return UserError(
            code="executor_unavailable",
            message="KitchenSoup could not reach the compute target.",
            action="Check the target service, network and credential reference. "
            "Check the retained attempt before retrying; it may still be running.",
        )
    return UserError(
        code=fallback,
        message="KitchenSoup could not complete this operation.",
        action="Review Advanced details and logs, correct the settings or input, "
        "and retry explicitly.",
    )
