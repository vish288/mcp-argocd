"""Tests for exceptions and the _err envelope that maps them."""

from __future__ import annotations

import json

import pytest

from mcp_argocd.exceptions import (
    ArgoCDApiError,
    ArgoCDAuthError,
    ArgoCDConflictError,
    ArgoCDError,
    ArgoCDNotFoundError,
    ArgoCDTimeoutError,
    ArgoCDWriteDisabledError,
)
from mcp_argocd.servers.argocd import _err

API = {"error", "status_code", "body"}
HINTED = API | {"hint"}


@pytest.mark.parametrize(
    ("exc", "keys"),
    [
        (ArgoCDError("base"), {"error"}),
        (ArgoCDApiError(400, "bad request"), API),
        (ArgoCDApiError(500, "Internal Server Error", None, "boom"), HINTED),
        (ArgoCDAuthError(401, "unauthorized"), HINTED),
        (ArgoCDAuthError(403, "permission denied: applications, sync, default/app"), HINTED),
        (ArgoCDNotFoundError(404, "not found"), HINTED),
        (ArgoCDConflictError(409, "another operation is already in progress", 10), HINTED),
        (ArgoCDWriteDisabledError(), {"error", "hint"}),
        (ArgoCDTimeoutError("timed out"), {"error", "hint"}),
    ],
    ids=["base", "api-400", "api-500", "auth-401", "auth-403", "404", "conflict", "ro", "timeout"],
)
def test_err_envelope_key_set(exc, keys):
    assert json.loads(_err(exc)).keys() == keys


def test_api_error_attrs():
    e = ArgoCDApiError(500, "broke", 2, "body-text")
    assert e.status_code == 500
    assert e.grpc_code == 2
    assert e.body == "body-text"
    assert "500" in str(e)


def test_permission_denied_hint_names_triple():
    e = ArgoCDAuthError(
        403,
        "permission denied",
        7,
        body='{"error":"permission denied: applications, sync, default/app","code":7}',
    )
    hint = json.loads(_err(e))["hint"]
    assert "argocd_can_i" in hint
    assert "applications" in hint
    assert "sync" in hint
    assert "default/app" in hint


def test_write_disabled_message():
    assert "ARGOCD_READ_ONLY" in str(ArgoCDWriteDisabledError())


def test_rollback_automated_hint():
    e = ArgoCDApiError(400, "Cannot rollback application with automated sync enabled")
    hint = json.loads(_err(e))["hint"]
    assert "auto-sync" in hint.lower()
    assert "argocd_patch_application" in hint


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (
            ArgoCDConflictError(400, "another operation is already in progress", 9),
            "argocd_get_operation",
        ),
        (ArgoCDApiError(400, "blocked by sync window"), "sync window"),
        (ArgoCDApiError(429, "slow down"), "Rate limited"),
        (ArgoCDApiError(502, "Unexpected HTML response from proxy"), "HTML"),
        (ArgoCDAuthError(403, "forbidden"), "argocd_can_i"),
        (ArgoCDAuthError(401, "nope"), "generate-token"),
    ],
    ids=["op-in-progress", "sync-window", "429", "html", "403-generic", "401"],
)
def test_api_hints(exc, needle):
    assert needle in json.loads(_err(exc))["hint"]
