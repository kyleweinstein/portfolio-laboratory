from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from webull_service.models import VerificationStage, VerificationState, utc_now
from webull_service.repository import MemoryRepository


def test_only_one_memory_verification_attempt_can_be_running() -> None:
    repository = MemoryRepository()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda _: repository.begin_verification_attempt(), range(16))
        )

    created = [attempt for attempt, was_created in results if was_created]
    attempt_ids = {attempt.attempt_id for attempt, _ in results}
    assert len(created) == 1
    assert len(attempt_ids) == 1


def test_stage_advancement_renews_the_seven_minute_lease() -> None:
    repository = MemoryRepository()
    attempt, created = repository.begin_verification_attempt()
    assert created is True

    advanced = repository.advance_verification_attempt(
        attempt.attempt_id, VerificationStage.VERIFYING_ACCESS
    )

    assert advanced.stage is VerificationStage.VERIFYING_ACCESS
    assert advanced.updated_at >= attempt.updated_at
    assert advanced.lease_expires_at > attempt.lease_expires_at
    assert advanced.lease_expires_at - advanced.updated_at == timedelta(minutes=7)


def test_status_reconciliation_times_out_an_expired_running_attempt() -> None:
    repository = MemoryRepository()
    attempt, _ = repository.begin_verification_attempt()
    repository.verification_attempts[-1] = attempt.model_copy(
        update={"lease_expires_at": utc_now() - timedelta(seconds=1)}
    )

    timed_out = repository.get_verification_attempt()

    assert timed_out is not None
    assert timed_out.state is VerificationState.TIMED_OUT
    assert timed_out.completed_at is not None
    assert timed_out.error is not None
    assert timed_out.error.code == "verification_timeout"
    replacement, created = repository.begin_verification_attempt()
    assert created is True
    assert replacement.attempt_id != attempt.attempt_id
    assert replacement.state is VerificationState.RUNNING
