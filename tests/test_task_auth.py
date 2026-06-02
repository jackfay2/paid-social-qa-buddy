"""Unit tests for Cloud Tasks OIDC verification, with an injected verifier."""

from __future__ import annotations

import pytest

from app.api.task_auth import (
    TaskAuthError,
    TaskAuthSettings,
    verify_cloud_task_request,
)

_SA = "ppc-qa-buddy@prj-prd-ai-ppc-qa-pkph.iam.gserviceaccount.com"


def _settings(auth_required: bool = True) -> TaskAuthSettings:
    return TaskAuthSettings(
        auth_required=auth_required,
        expected_audience="https://qa-buddy-worker-social.run.app/tasks/qa/run",
        expected_service_account_email=_SA,
    )


def _verifier(claims: dict):
    def verify(token: str, audience: str) -> dict:
        return claims
    return verify


def test_skips_when_auth_not_required() -> None:
    # No header, but auth off -> no raise.
    verify_cloud_task_request({}, _settings(auth_required=False))


def test_fail_closed_when_audience_unconfigured() -> None:
    """Audit #3: auth_required but empty audience must REFUSE (empty audience
    would make the verifier skip the audience check entirely)."""
    s = TaskAuthSettings(auth_required=True, expected_audience="", expected_service_account_email=_SA)
    with pytest.raises(TaskAuthError):
        verify_cloud_task_request(
            {"Authorization": "Bearer x"}, s, verifier=_verifier({"email_verified": True, "email": _SA})
        )


def test_fail_closed_when_sa_email_unconfigured() -> None:
    """Audit #3: auth_required but empty expected SA email must REFUSE."""
    s = TaskAuthSettings(
        auth_required=True,
        expected_audience="https://qa-buddy-worker-social.run.app/tasks/qa/run",
        expected_service_account_email="",
    )
    with pytest.raises(TaskAuthError):
        verify_cloud_task_request(
            {"Authorization": "Bearer x"}, s, verifier=_verifier({"email_verified": True, "email": _SA})
        )


def test_missing_authorization_header_raises() -> None:
    with pytest.raises(TaskAuthError):
        verify_cloud_task_request({}, _settings())


def test_non_bearer_header_raises() -> None:
    with pytest.raises(TaskAuthError):
        verify_cloud_task_request({"Authorization": "Token abc"}, _settings())


def test_empty_bearer_token_raises() -> None:
    with pytest.raises(TaskAuthError):
        verify_cloud_task_request({"Authorization": "Bearer "}, _settings())


def test_valid_token_passes() -> None:
    claims = {"email": _SA, "email_verified": True}
    # Should not raise.
    verify_cloud_task_request(
        {"Authorization": "Bearer good-token"},
        _settings(),
        verifier=_verifier(claims),
    )


def test_verifier_exception_raises_auth_error() -> None:
    def bad_verify(token: str, audience: str) -> dict:
        raise ValueError("invalid signature")

    with pytest.raises(TaskAuthError):
        verify_cloud_task_request(
            {"Authorization": "Bearer x"}, _settings(), verifier=bad_verify
        )


def test_service_account_mismatch_raises() -> None:
    claims = {"email": "attacker@evil.example.com", "email_verified": True}
    with pytest.raises(TaskAuthError):
        verify_cloud_task_request(
            {"Authorization": "Bearer x"}, _settings(), verifier=_verifier(claims)
        )


def test_email_not_verified_raises() -> None:
    claims = {"email": _SA, "email_verified": False}
    with pytest.raises(TaskAuthError):
        verify_cloud_task_request(
            {"Authorization": "Bearer x"}, _settings(), verifier=_verifier(claims)
        )


def test_lowercase_authorization_header_accepted() -> None:
    claims = {"email": _SA, "email_verified": True}
    # Should not raise — header lookup is case-tolerant.
    verify_cloud_task_request(
        {"authorization": "Bearer good-token"},
        _settings(),
        verifier=_verifier(claims),
    )
