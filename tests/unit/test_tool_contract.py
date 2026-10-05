"""Wire contract for every ``@mcp.tool``: the request it sends, the envelope it returns.

One row per tool: ``(tool_name, call_args, method, path, expected_query, expected_json_body)``.
``path`` is under ``/api/v1`` unless it already starts with ``/api/`` (``/api/version``).
Rows are derived by reading each tool body and the ``ArgoCDClient`` method it calls.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

import pytest
from httpx import Response

APP = "/applications/guestbook"
RES_QUERY = {"name": "web", "group": "", "version": "v1", "kind": "Deployment"}
REPO = "https://github.com/argoproj/argocd-example-apps.git"
REPO_ENC = quote(REPO, safe="")

CREATE_BODY = {
    "metadata": {"name": "x"},
    "spec": {
        "project": "default",
        "source": {"repoURL": "https://r", "targetRevision": "HEAD", "path": "guestbook"},
        "destination": {"server": "https://k", "namespace": "default"},
    },
}

ROWS = [
    # 2.1 Applications — read (14)
    ("argocd_list_applications", {}, "GET", "/applications", {}, None),
    ("argocd_get_application", {"name": "guestbook"}, "GET", APP, {}, None),
    ("argocd_get_resource_tree", {"name": "guestbook"}, "GET", f"{APP}/resource-tree", {}, None),
    (
        "argocd_get_managed_resources",
        {"name": "guestbook"},
        "GET",
        f"{APP}/managed-resources",
        {},
        None,
    ),
    (
        "argocd_get_resource",
        {"name": "guestbook", "kind": "Deployment", "resource_name": "web"},
        "GET",
        f"{APP}/resource",
        RES_QUERY,
        None,
    ),
    ("argocd_get_manifests", {"name": "guestbook"}, "GET", f"{APP}/manifests", {}, None),
    ("argocd_get_application_events", {"name": "guestbook"}, "GET", f"{APP}/events", {}, None),
    (
        "argocd_get_pod_logs",
        {"name": "guestbook"},
        "GET",
        f"{APP}/logs",
        {"tailLines": "200"},
        None,
    ),
    ("argocd_get_application_history", {"name": "guestbook"}, "GET", APP, {}, None),
    (
        "argocd_get_revision_metadata",
        {"name": "guestbook", "revision": "abc"},
        "GET",
        f"{APP}/revisions/abc/metadata",
        {},
        None,
    ),
    ("argocd_get_operation", {"name": "guestbook"}, "GET", APP, {}, None),
    ("argocd_wait_for_operation", {"name": "guestbook"}, "GET", APP, {}, None),
    ("argocd_get_sync_windows", {"name": "guestbook"}, "GET", f"{APP}/syncwindows", {}, None),
    (
        "argocd_list_resource_actions",
        {"name": "guestbook", "kind": "Deployment", "resource_name": "web"},
        "GET",
        f"{APP}/resource/actions",
        RES_QUERY,
        None,
    ),
    # 2.2 Applications — write (8)
    (
        "argocd_sync_application",
        {"name": "guestbook"},
        "POST",
        f"{APP}/sync",
        {},
        {"name": "guestbook"},
    ),
    (
        "argocd_rollback_application",
        {"name": "guestbook", "history_id": 2},
        "POST",
        f"{APP}/rollback",
        {},
        {"name": "guestbook", "id": 2},
    ),
    (
        "argocd_terminate_operation",
        {"name": "guestbook"},
        "DELETE",
        f"{APP}/operation",
        {},
        None,
    ),
    (
        "argocd_create_application",
        {
            "name": "x",
            "project": "default",
            "repo_url": "https://r",
            "path": "guestbook",
            "dest_server": "https://k",
            "dest_namespace": "default",
        },
        "POST",
        "/applications",
        {},
        CREATE_BODY,
    ),
    (
        "argocd_patch_application",
        {"name": "guestbook", "patch": '{"spec":{}}'},
        "PATCH",
        APP,
        {},
        {"name": "guestbook", "patch": '{"spec":{}}', "patchType": "merge"},
    ),
    (
        "argocd_delete_application",
        {"name": "guestbook"},
        "DELETE",
        APP,
        {"cascade": "true", "propagationPolicy": "foreground"},
        None,
    ),
    (
        "argocd_run_resource_action",
        {"name": "guestbook", "action": "restart", "kind": "Deployment", "resource_name": "web"},
        "POST",
        f"{APP}/resource/actions/v2",
        {
            "resourceName": "web",
            "version": "v1",
            "group": "",
            "kind": "Deployment",
            "action": "restart",
        },
        None,
    ),
    (
        "argocd_delete_resource",
        {"name": "guestbook", "kind": "Deployment", "resource_name": "web"},
        "DELETE",
        f"{APP}/resource",
        {"name": "web", "version": "v1", "group": "", "kind": "Deployment"},
        None,
    ),
    # 2.3 ApplicationSets (3)
    ("argocd_list_applicationsets", {}, "GET", "/applicationsets", {}, None),
    (
        "argocd_get_applicationset",
        {"name": "guestbooks"},
        "GET",
        "/applicationsets/guestbooks",
        {},
        None,
    ),
    (
        "argocd_generate_applicationset",
        {"applicationset": '{"kind":"ApplicationSet"}'},
        "POST",
        "/applicationsets/generate",
        {},
        {"applicationSet": {"kind": "ApplicationSet"}},
    ),
    # 2.4 Projects (2)
    ("argocd_list_projects", {}, "GET", "/projects", {}, None),
    ("argocd_get_project", {"name": "default"}, "GET", "/projects/default", {}, None),
    # 2.5 Clusters (3)
    ("argocd_list_clusters", {}, "GET", "/clusters", {}, None),
    (
        "argocd_get_cluster",
        {"cluster": "in-cluster"},
        "GET",
        "/clusters/in-cluster",
        {"id.type": "name"},
        None,
    ),
    (
        "argocd_invalidate_cluster_cache",
        {"cluster": "in-cluster"},
        "POST",
        "/clusters/in-cluster/invalidate-cache",
        {"id.type": "name"},
        None,
    ),
    # 2.6 Repositories (3)
    ("argocd_list_repositories", {}, "GET", "/repositories", {}, None),
    (
        "argocd_get_repository_refs",
        {"repo": REPO},
        "GET",
        f"/repositories/{REPO_ENC}/refs",
        {},
        None,
    ),
    (
        "argocd_list_repository_apps",
        {"repo": REPO},
        "GET",
        f"/repositories/{REPO_ENC}/apps",
        {},
        None,
    ),
    # 2.7 Server and account (4)
    ("argocd_get_version", {}, "GET", "/api/version", {}, None),
    ("argocd_get_userinfo", {}, "GET", "/session/userinfo", {}, None),
    (
        "argocd_can_i",
        {"resource": "applications", "action": "get"},
        "GET",
        "/account/can-i/applications/get/*/*",
        {},
        None,
    ),
    ("argocd_get_settings", {}, "GET", "/settings", {}, None),
]

WRITE_NAMES = {
    "argocd_sync_application",
    "argocd_rollback_application",
    "argocd_terminate_operation",
    "argocd_create_application",
    "argocd_patch_application",
    "argocd_delete_application",
    "argocd_run_resource_action",
    "argocd_delete_resource",
    "argocd_invalidate_cluster_cache",
}
WRITE_ROWS = [r for r in ROWS if r[0] in WRITE_NAMES]

NOT_FOUND_KEYS = {"error", "status_code", "body", "hint"}


def _full_path(path: str) -> str:
    return path if path.startswith("/api/") else f"/api/v1{path}"


def _parse(result) -> dict:
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
async def test_request_shape(tool_client, row):
    name, args, method, path, query, body = row
    client, router = tool_client
    route = router.request(method, _full_path(path)).mock(return_value=Response(200, json={}))
    # rollback and terminate pre-read the app; satisfy that GET.
    router.get(_full_path(APP)).mock(return_value=Response(200, json={}))

    parsed = _parse(await client.call_tool(name, args))

    assert "error" not in parsed, parsed
    req = route.calls.last.request
    assert req.method == method
    assert req.url.raw_path.split(b"?")[0].decode() == _full_path(path)
    assert dict(req.url.params) == query
    assert (json.loads(req.content) if req.content else None) == body


@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
async def test_forced_failure(tool_client, row):
    name, args, method, path, _query, _body = row
    client, router = tool_client
    router.get(_full_path(APP)).mock(return_value=Response(200, json={}))
    router.request(method, _full_path(path)).mock(
        return_value=Response(404, json={"message": "not found", "code": 5})
    )

    parsed = _parse(await client.call_tool(name, args))

    assert NOT_FOUND_KEYS <= parsed.keys()
    assert parsed["status_code"] == 404


async def test_forced_permission_denied(tool_client):
    client, router = tool_client
    router.get(_full_path(APP)).mock(
        return_value=Response(
            403,
            json={
                "error": "permission denied: applications, sync, default/app",
                "code": 7,
                "message": "permission denied",
            },
        )
    )

    parsed = _parse(await client.call_tool("argocd_get_application", {"name": "guestbook"}))

    assert parsed["status_code"] == 403
    hint = parsed["hint"]
    assert "argocd_can_i" in hint
    assert "applications" in hint and "sync" in hint and "default/app" in hint


@pytest.mark.parametrize("row", WRITE_ROWS, ids=[r[0] for r in WRITE_ROWS])
async def test_write_blocked_in_read_only(readonly_client, row):
    name, args, *_ = row
    client, _router = readonly_client
    parsed = _parse(await client.call_tool(name, args))
    assert "read-only" in parsed["hint"]


async def test_every_tool_has_a_row(tool_client):
    client, _router = tool_client
    assert {r[0] for r in ROWS} == {t.name for t in await client.list_tools()}


async def test_bug_is_a_tool_error_not_a_result(tool_client, caplog):
    client, router = tool_client
    router.get(_full_path("/applications")).mock(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR, logger="mcp_argocd.servers.argocd"):
        result = await client.call_tool("argocd_list_applications", {}, raise_on_error=False)

    assert result.is_error is True
    assert "RuntimeError: boom" in result.content[0].text


async def test_api_error_is_still_a_result(tool_client):
    client, router = tool_client
    router.get(_full_path(APP)).mock(return_value=Response(404, json={"message": "gone"}))

    result = await client.call_tool(
        "argocd_get_application", {"name": "guestbook"}, raise_on_error=False
    )

    assert result.is_error is False
    assert "hint" in json.loads(result.content[0].text)
