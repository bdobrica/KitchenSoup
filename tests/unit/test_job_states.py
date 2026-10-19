import pytest

from app.jobs.states import TERMINAL, JobState, validate_transition

# Expected edges from ADR 0014; independent of the implementation transition map.
EDGES = {
    "QUEUED": {"PREPARING", "CANCELED"},
    "PREPARING": {"SUBMITTED", "FAILED", "CANCEL_REQUESTED"},
    "SUBMITTED": {"RUNNING", "FAILED", "CANCEL_REQUESTED", "LOST"},
    "RUNNING": {"POSTPROCESSING", "FAILED", "CANCEL_REQUESTED", "LOST"},
    "POSTPROCESSING": {"SUCCEEDED", "FAILED"},
    "CANCEL_REQUESTED": {"CANCELED", "POSTPROCESSING", "FAILED", "LOST"},
    "SUCCEEDED": set(),
    "FAILED": set(),
    "CANCELED": set(),
    "LOST": set(),
}


@pytest.mark.parametrize("previous", list(JobState))
@pytest.mark.parametrize("following", list(JobState))
def test_transition_matrix(previous: JobState, following: JobState) -> None:
    if previous == following or following in EDGES[previous]:
        validate_transition(previous, following)
    else:
        with pytest.raises(ValueError, match="Invalid job transition"):
            validate_transition(previous, following)


def test_terminal_states_are_final_and_unknown_observations_are_not_states() -> None:
    assert TERMINAL == {"SUCCEEDED", "FAILED", "CANCELED", "LOST"}
    with pytest.raises(ValueError):
        validate_transition("RUNNING", "MISSING")
