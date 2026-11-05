"""The slot worker: review and verify go to the P05 services, the rest to the base (P05).

The controller calls ``perform`` once per slot. The review slot runs the
read-only reviewer and maps the verdict to ``review.approved`` or
``review.changes_requested``; the verify slot builds a receipt and maps it to
``verify.passed`` or ``verify.failed``. Every other slot is the base worker's.
The controller, not this module, applies the event through the workflow.
"""

from collections.abc import Callable

from hyper_agents.application.controller import StepOutcome, StepRequest, StepWorkerPort
from hyper_agents.application.review import ReadOnlyWorkspace, ReviewerPort, ReviewService
from hyper_agents.application.verify import CheckResult, VerificationService
from hyper_agents.domain.workflow import Slot


class ReviewVerifyWorker:
    def __init__(
        self,
        base: StepWorkerPort,
        *,
        reviewer: ReviewerPort,
        workspace: ReadOnlyWorkspace,
        review: ReviewService,
        verify: VerificationService,
        results: Callable[[str], list[CheckResult]],
        criteria: tuple[str, ...],
    ) -> None:
        self._base = base
        self._reviewer = reviewer
        self._workspace = workspace
        self._review = review
        self._verify = verify
        self._results = results
        self._criteria = criteria

    def perform(self, request: StepRequest) -> object:
        if request.slot is Slot.REVIEW:
            result = self._review.review(self._reviewer, self._workspace, request.head_sha)
            event = (
                "review.approved"
                if result.verdict.verdict == "approve"
                else "review.changes_requested"
            )
            return StepOutcome(event, result.tokens_used, request.head_sha)
        if request.slot is Slot.VERIFY:
            receipt = self._verify.verify(
                request.head_sha,
                self._criteria,
                self._results(request.head_sha),
                expected_sha=request.head_sha,
            )
            event = "verify.passed" if receipt.passed else "verify.failed"
            return StepOutcome(event, 0, request.head_sha)
        return self._base.perform(request)


__all__ = ["ReviewVerifyWorker"]
