from hyper_agents.domain.errors import RETRYABLE, ErrorCategory, HyperAgentsError, PolicyDenied


def test_only_transient_categories_are_retryable() -> None:
    assert {ErrorCategory.PROVIDER_UNAVAILABLE, ErrorCategory.WORKER_FAILED} == RETRYABLE
    assert not PolicyDenied("no").retryable
    assert HyperAgentsError("x", ErrorCategory.PROVIDER_UNAVAILABLE).retryable
