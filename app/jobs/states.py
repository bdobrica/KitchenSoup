"""Normalized control-plane states; executor observations are separate."""

from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    SUBMITTED = "SUBMITTED"
    RUNNING = "RUNNING"
    POSTPROCESSING = "POSTPROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    LOST = "LOST"


TERMINAL = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED, JobState.LOST})
TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.PREPARING, JobState.CANCELED}),
    JobState.PREPARING: frozenset({JobState.SUBMITTED, JobState.FAILED, JobState.CANCEL_REQUESTED}),
    JobState.SUBMITTED: frozenset(
        {JobState.RUNNING, JobState.FAILED, JobState.CANCEL_REQUESTED, JobState.LOST}
    ),
    JobState.RUNNING: frozenset(
        {JobState.POSTPROCESSING, JobState.FAILED, JobState.CANCEL_REQUESTED, JobState.LOST}
    ),
    JobState.POSTPROCESSING: frozenset({JobState.SUCCEEDED, JobState.FAILED}),
    JobState.CANCEL_REQUESTED: frozenset(
        {JobState.CANCELED, JobState.POSTPROCESSING, JobState.FAILED, JobState.LOST}
    ),
    **{state: frozenset() for state in TERMINAL},
}


def validate_transition(previous: str, following: str) -> None:
    old, new = JobState(previous), JobState(following)
    if old != new and new not in TRANSITIONS[old]:
        raise ValueError(f"Invalid job transition: {old} -> {new}")
