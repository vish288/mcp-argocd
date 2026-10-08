"""Behaviour tests for the tools, on real-shaped fixtures via the in-memory client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from httpx import Response

from mcp_argocd.servers import argocd as srv

FIX = Path(__file__).resolve().parents[1] / "fixtures"
V1 = "/api/v1"


def fx(name: str):
    return json.loads((FIX / name).read_text())


def parse(result):
    return json.loads(result.content[0].text)


async def call(client, name, args):
    return parse(await client.call_tool(name, args))


# ── list_applications ──────────────────────────────────────────────


class TestListApplications:
    async def _list(self, tool_client, args):
        client, router = tool_client
        router.get(f"{V1}/applications").mock(
            return_value=Response(200, json=fx("application_list.json"))
        )
        return await call(client, "argocd_list_applications", args)

    async def test_slim_rows(self, tool_client):
        out = await self._list(tool_client, {})
        assert out["total"] == 3
        row = next(r for r in out["items"] if r["name"] == "guestbook")
        assert row["sync_status"] == "Synced"
        assert row["health_status"] == "Healthy"
        assert row["source"]["path"] == "guestbook"
        assert row["destination"]["server"] == "https://kubernetes.default.svc"
        assert row["auto_sync"] is True and row["self_heal"] is True
        assert row["revision"] == "abc123def456"  # 12 chars

    async def test_sync_status_filter(self, tool_client):
        out = await self._list(tool_client, {"sync_status": "OutOfSync"})
        assert [r["name"] for r in out["items"]] == ["payments"]

    async def test_health_filter(self, tool_client):
        out = await self._list(tool_client, {"health_status": "Progressing"})
        assert [r["name"] for r in out["items"]] == ["api"]

    async def test_name_prefix_and_sorting(self, tool_client):
        out = await self._list(tool_client, {"name_prefix": "a"})
        assert [r["name"] for r in out["items"]] == ["api"]

    async def test_destination_filter(self, tool_client):
        out = await self._list(tool_client, {"destination": "https://kubernetes.default.svc"})
        assert out["total"] == 3

    async def test_paging(self, tool_client):
        out = await self._list(tool_client, {"limit": 1, "offset": 0})
        assert out["count"] == 1 and out["total"] == 3
        assert out["has_more"] is True and out["next_offset"] == 1


# ── get_application ────────────────────────────────────────────────


class TestGetApplication:
    async def _get(self, tool_client, args):
        client, router = tool_client
        router.get(f"{V1}/applications/guestbook").mock(
            return_value=Response(200, json=fx("application.json"))
        )
        return (
            client,
            router,
            await call(client, "argocd_get_application", {"name": "guestbook", **args}),
        )

    async def test_slim_strips_last_applied_and_counts(self, tool_client):
        _c, _r, out = await self._get(tool_client, {})
        assert srv.LAST_APPLIED not in out["metadata"]["annotations"]
        counts = out["status"]["resource_counts"]
        assert counts == {"total": 3, "out_of_sync": 1, "unhealthy": 1}
        assert out["status"]["history_count"] == 2

    async def test_full_returns_raw(self, tool_client):
        _c, _r, out = await self._get(tool_client, {"full": True})
        assert out["metadata"]["managedFields"]  # present in raw, absent in slim

    async def test_resources_mode_unhealthy(self, tool_client):
        _c, _r, out = await self._get(tool_client, {"resources": "unhealthy"})
        res = out["status"]["resources"]
        assert [r["kind"] for r in res] == ["Pod"]

    async def test_refresh_hard_query(self, tool_client):
        client, router, _out = await self._get(tool_client, {"refresh": "hard"})
        assert dict(router.calls.last.request.url.params) == {"refresh": "hard"}

    async def test_namespace_name_split_sets_app_namespace(self, tool_client):
        client, router = tool_client
        route = router.get(f"{V1}/applications/guestbook").mock(
            return_value=Response(200, json=fx("application.json"))
        )
        await call(client, "argocd_get_application", {"name": "team-a/guestbook"})
        assert dict(route.calls.last.request.url.params) == {"appNamespace": "team-a"}


# ── resource tree, managed resources, resource, manifests ──────────


async def test_resource_tree_slim_and_filters(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook/resource-tree").mock(
        return_value=Response(200, json=fx("resource_tree.json"))
    )
    out = await call(client, "argocd_get_resource_tree", {"name": "guestbook"})
    node = out["items"][0]
    assert "networkingInfo" not in node and "uid" not in node
    pod = next(n for n in out["items"] if n["kind"] == "Pod")
    assert pod["parent"]["kind"] == "Deployment"
    # kind filter
    out = await call(client, "argocd_get_resource_tree", {"name": "guestbook", "kind": "Pod"})
    assert all(n["kind"] == "Pod" for n in out["items"])
    # orphaned included
    out = await call(
        client, "argocd_get_resource_tree", {"name": "guestbook", "include_orphaned": True}
    )
    assert any(n["kind"] == "ConfigMap" for n in out["items"])


async def test_managed_resources_modified_and_truncation(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook/managed-resources").mock(
        return_value=Response(200, json=fx("managed_resources.json"))
    )
    out = await call(client, "argocd_get_managed_resources", {"name": "guestbook"})
    assert out["count"] == 1  # unmodified Service dropped
    item = out["items"][0]
    assert item["diff_truncated"] is True
    assert len(item["diff"]) == 4000
    # include_states strips managedFields
    out = await call(
        client,
        "argocd_get_managed_resources",
        {"name": "guestbook", "include_states": True, "max_diff_chars": 10},
    )
    assert "managedFields" not in json.dumps(out["items"][0]["targetState"])


async def test_get_resource_strips_managed_fields(tool_client):
    client, router = tool_client
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "cm",
            "managedFields": [{"manager": "x"}],
            "annotations": {srv.LAST_APPLIED: "{}", "keep": "yes"},
        },
    }
    route = router.get(f"{V1}/applications/guestbook/resource").mock(
        return_value=Response(200, json={"manifest": json.dumps(manifest)})
    )
    out = await call(
        client,
        "argocd_get_resource",
        {"name": "guestbook", "kind": "ConfigMap", "resource_name": "cm"},
    )
    meta = out["manifest"]["metadata"]
    assert "managedFields" not in meta
    assert srv.LAST_APPLIED not in meta["annotations"]
    assert meta["annotations"]["keep"] == "yes"
    # AR-R01: the swagger names this query param `resourceName`, not `name`
    params = dict(route.calls.last.request.url.params)
    assert params["resourceName"] == "cm"
    assert "name" not in params
    assert params["kind"] == "ConfigMap"


async def test_manifests_parse_and_filter(tool_client):
    client, router = tool_client
    payload = {
        "revision": "abc",
        "sourceType": "Helm",
        "manifests": [
            json.dumps({"kind": "Deployment", "metadata": {"name": "web"}}),
            json.dumps({"kind": "Service", "metadata": {"name": "web"}}),
        ],
    }
    router.get(f"{V1}/applications/guestbook/manifests").mock(
        return_value=Response(200, json=payload)
    )
    out = await call(client, "argocd_get_manifests", {"name": "guestbook", "kind": "Service"})
    assert out["source_type"] == "Helm"
    assert [m["kind"] for m in out["manifests"]] == ["Service"]


async def test_events_warnings_sort_and_cap(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook/events").mock(
        return_value=Response(200, json=fx("events.json"))
    )
    out = await call(
        client, "argocd_get_application_events", {"name": "guestbook", "warnings_only": True}
    )
    assert out["total"] == 1
    ev = out["items"][0]
    assert ev["type"] == "Warning"
    assert len(ev["message"]) == 500


async def test_pod_logs_stream(tool_client):
    client, router = tool_client
    ndjson = (FIX / "logs.ndjson").read_text()
    router.get(f"{V1}/applications/guestbook/logs").mock(return_value=Response(200, text=ndjson))
    out = await call(client, "argocd_get_pod_logs", {"name": "guestbook", "tail_lines": 5})
    assert out["shown_lines"] == 3  # stops before the last:true marker
    assert out["pods"] == ["guestbook-ui-xyz"]
    assert out["lines"][2]["content"] == "ERROR connection refused"


async def test_history_newest_first(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook").mock(
        return_value=Response(200, json=fx("application.json"))
    )
    out = await call(client, "argocd_get_application_history", {"name": "guestbook"})
    assert [h["id"] for h in out["items"]] == [3, 2]
    assert out["current_revision"] == "abc123def4567890feed"


async def test_revision_metadata(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook/revisions/abc/metadata").mock(
        return_value=Response(
            200, json={"author": "alice", "date": "d", "message": "m", "tags": ["v1"]}
        )
    )
    out = await call(
        client, "argocd_get_revision_metadata", {"name": "guestbook", "revision": "abc"}
    )
    assert out["author"] == "alice" and out["tags"] == ["v1"]


async def test_get_operation_slim_and_null(tool_client):
    client, router = tool_client
    route = router.get(f"{V1}/applications/guestbook")
    route.mock(return_value=Response(200, json=fx("application.json")))
    out = await call(client, "argocd_get_operation", {"name": "guestbook"})
    assert out["phase"] == "Succeeded"
    assert out["result"]["counts"]["SyncFailed"] == 1
    assert out["result"]["failed"][0]["kind"] == "Service"
    # no operation
    route.mock(return_value=Response(200, json={"status": {}}))
    out = await call(client, "argocd_get_operation", {"name": "guestbook"})
    assert out == {"phase": None}


# ── wait_for_operation ─────────────────────────────────────────────


def _app_phase(phase):
    return {
        "status": {
            "operationState": {"phase": phase},
            "health": {"status": "Healthy"},
            "sync": {"status": "Synced"},
        }
    }


class TestWaitForOperation:
    @pytest.fixture(autouse=True)
    def _fast_clock(self, monkeypatch):
        clock = {"t": 0.0}
        monkeypatch.setattr(srv.time, "monotonic", lambda: clock["t"])

        async def fake_sleep(s):
            clock["t"] += s

        monkeypatch.setattr(srv.asyncio, "sleep", fake_sleep)

    async def test_reaches_terminal(self, tool_client):
        client, router = tool_client
        router.get(f"{V1}/applications/guestbook").mock(
            side_effect=[
                Response(200, json=_app_phase("Running")),
                Response(200, json=_app_phase("Running")),
                Response(200, json=_app_phase("Succeeded")),
            ]
        )
        out = await call(
            client, "argocd_wait_for_operation", {"name": "guestbook", "poll_interval_seconds": 3}
        )
        assert out["phase"] == "Succeeded"
        assert out["timed_out"] is False
        assert out["sync_status"] == "Synced"

    async def test_times_out(self, tool_client):
        client, router = tool_client
        router.get(f"{V1}/applications/guestbook").mock(
            return_value=Response(200, json=_app_phase("Running"))
        )
        out = await call(
            client,
            "argocd_wait_for_operation",
            {"name": "guestbook", "timeout_seconds": 10, "poll_interval_seconds": 3},
        )
        assert out["timed_out"] is True
        assert out["phase"] == "Running"


# ── write tools ────────────────────────────────────────────────────


async def test_sync_body_assembly(tool_client):
    client, router = tool_client
    route = router.post(f"{V1}/applications/guestbook/sync").mock(
        return_value=Response(200, json={})
    )
    await call(
        client,
        "argocd_sync_application",
        {
            "name": "guestbook",
            "prune": True,
            "force": True,
            "apply_out_of_sync_only": True,
            "resources": ["apps:Deployment:web/default"],
            "sync_options": ["ServerSideApply=true"],
        },
    )
    body = json.loads(route.calls.last.request.content)
    assert body["prune"] is True
    assert body["strategy"] == {"apply": {"force": True}}
    assert body["syncOptions"]["items"] == ["ServerSideApply=true", "ApplyOutOfSyncOnly=true"]
    assert body["resources"] == [
        {"group": "apps", "kind": "Deployment", "name": "web", "namespace": "default"}
    ]


def _fast_clock(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(srv.time, "monotonic", lambda: clock["t"])

    async def fake_sleep(s):
        clock["t"] += s

    monkeypatch.setattr(srv.asyncio, "sleep", fake_sleep)


# AR-R04: POST /sync returns the new request in top-level `operation` while
# status.operationState still describes the previous, finished run. The old code
# reported that stale state (and wait=True returned at once); the fix must poll
# until the *new* operation lands.
_STALE = {
    "operation": {"sync": {"revision": "newrev"}},
    "status": {
        "operationState": {
            "phase": "Succeeded",
            "startedAt": "2000-01-01T00:00:00Z",
            "operation": {"sync": {"revision": "oldrev"}},
        },
        "health": {"status": "Healthy"},
        "sync": {"status": "Synced", "revision": "oldrev"},
    },
}
_PENDING = _STALE  # first poll: new op still queued (top-level operation present)
_NEW_DONE = {
    "status": {
        "operationState": {
            "phase": "Succeeded",
            "startedAt": "2030-01-01T00:00:00Z",
            "operation": {"sync": {"revision": "newrev"}},
        },
        "health": {"status": "Healthy"},
        "sync": {"status": "Synced", "revision": "newrev"},
    }
}


async def test_sync_no_wait_reports_requested_not_stale(tool_client):
    client, router = tool_client
    router.post(f"{V1}/applications/guestbook/sync").mock(return_value=Response(200, json=_STALE))
    out = await call(client, "argocd_sync_application", {"name": "guestbook"})
    assert out["phase"] == "Requested"
    assert out["requested"] == {"sync": {"revision": "newrev"}}


async def test_sync_with_wait_polls_past_stale(tool_client, monkeypatch):
    _fast_clock(monkeypatch)
    client, router = tool_client
    router.post(f"{V1}/applications/guestbook/sync").mock(return_value=Response(200, json=_STALE))
    route = router.get(f"{V1}/applications/guestbook").mock(
        side_effect=[
            Response(200, json=_PENDING),  # new op still queued -> keep polling
            Response(200, json=_NEW_DONE),  # new op finished -> report this one
        ]
    )
    out = await call(client, "argocd_sync_application", {"name": "guestbook", "wait": True})
    assert out["phase"] == "Succeeded" and out["timed_out"] is False
    assert out["sync"]["revision"] == "newrev"  # the new op, not the stale "oldrev"
    assert route.call_count == 2  # did not return the stale terminal state at once


async def test_rollback_refused_when_automated(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook").mock(
        return_value=Response(200, json=fx("application.json"))  # has automated sync
    )
    out = await call(client, "argocd_rollback_application", {"name": "guestbook", "history_id": 2})
    assert out["status_code"] == 400
    assert "auto-sync" in out["hint"].lower()


async def test_rollback_proceeds_without_automated(tool_client):
    client, router = tool_client
    app = fx("application.json")
    app["spec"]["syncPolicy"] = {}
    router.get(f"{V1}/applications/guestbook").mock(return_value=Response(200, json=app))
    route = router.post(f"{V1}/applications/guestbook/rollback").mock(
        return_value=Response(200, json={})
    )
    out = await call(client, "argocd_rollback_application", {"name": "guestbook", "history_id": 2})
    assert json.loads(route.calls.last.request.content) == {"name": "guestbook", "id": 2}
    assert out["phase"] == "Requested"  # AR-R04: the new request, not the stale op
    assert out["rolled_back_to"] == {"id": 2, "revision": "0ldrev0ldrev0ldrev00"}


async def test_terminate_reports_previous_phase(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook").mock(
        return_value=Response(200, json=fx("application.json"))
    )
    router.delete(f"{V1}/applications/guestbook/operation").mock(
        return_value=Response(200, json={})
    )
    out = await call(client, "argocd_terminate_operation", {"name": "guestbook"})
    assert out == {"status": "terminating", "previous_phase": "Succeeded"}


async def test_create_application_body(tool_client):
    client, router = tool_client
    route = router.post(f"{V1}/applications").mock(return_value=Response(200, json={}))
    await call(
        client,
        "argocd_create_application",
        {
            "name": "preview",
            "project": "default",
            "repo_url": "https://r",
            "path": "app",
            "dest_server": "https://k",
            "dest_namespace": "preview",
            "helm_parameters": ["image.tag=v2"],
            "labels": {"env": "preview"},
            "auto_sync": True,
            "prune": True,
            "create_namespace": True,
        },
    )
    body = json.loads(route.calls.last.request.content)
    assert body["metadata"]["labels"] == {"env": "preview"}
    assert body["spec"]["source"]["helm"]["parameters"] == [{"name": "image.tag", "value": "v2"}]
    assert body["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": False}
    assert "CreateNamespace=true" in body["spec"]["syncPolicy"]["syncOptions"]


async def test_patch_body(tool_client):
    client, router = tool_client
    route = router.patch(f"{V1}/applications/guestbook").mock(return_value=Response(200, json={}))
    await call(
        client,
        "argocd_patch_application",
        {"name": "guestbook", "patch": '{"spec":{}}', "patch_type": "json"},
    )
    body = json.loads(route.calls.last.request.content)
    assert body["patchType"] == "json"


# ── applicationsets ────────────────────────────────────────────────


async def test_list_applicationsets_slim(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applicationsets").mock(
        return_value=Response(200, json={"items": [fx("applicationset.json")]})
    )
    out = await call(client, "argocd_list_applicationsets", {})
    row = out["items"][0]
    assert row["name"] == "guestbooks"
    assert set(row["generators"]) == {"git", "list"}
    assert row["strategy"] == "RollingSync"
    assert row["health_status"] == "Healthy"


async def test_generate_applicationset(tool_client):
    client, router = tool_client
    gen = {
        "applications": [
            {
                "metadata": {"name": "a1"},
                "spec": {
                    "project": "p",
                    "source": {"repoURL": "r", "path": "x"},
                    "destination": {"server": "s"},
                },
            }
        ]
    }
    route = router.post(f"{V1}/applicationsets/generate").mock(return_value=Response(200, json=gen))
    out = await call(
        client, "argocd_generate_applicationset", {"applicationset": '{"kind":"ApplicationSet"}'}
    )
    assert out["count"] == 1 and out["applications"][0]["name"] == "a1"
    assert json.loads(route.calls.last.request.content) == {
        "applicationSet": {"kind": "ApplicationSet"}
    }


async def test_generate_rejects_non_json(tool_client):
    client, _router = tool_client
    out = await call(client, "argocd_generate_applicationset", {"applicationset": "not json"})
    assert out["status_code"] == 400


# ── scrubbing (repos, clusters, projects) ──────────────────────────

SECRET_MARKERS = (
    "SECRET",
    "SECRET-TOKEN",
    "bearerToken",
    "sshPrivateKey",
    "password",
    "jwtTokens",
    '"config"',
)


async def test_repositories_scrubbed(tool_client):
    client, router = tool_client
    router.get(f"{V1}/repositories").mock(
        return_value=Response(200, json={"items": [fx("repository.json")]})
    )
    result = await client.call_tool("argocd_list_repositories", {})
    text = result.content[0].text
    assert "SECRET" not in text and "bearerToken" not in text
    assert parse(result)["items"][0]["has_credentials"] is True


async def test_cluster_config_removed_even_full(tool_client):
    client, router = tool_client
    router.get(f"{V1}/clusters/in-cluster").mock(
        return_value=Response(200, json=fx("cluster.json"))
    )
    result = await client.call_tool("argocd_get_cluster", {"cluster": "in-cluster", "full": True})
    text = result.content[0].text
    assert "config" not in text and "SECRET-TOKEN" not in text
    out = parse(result)
    assert out["serverVersion"] == "1.30"  # raw key preserved in full view


async def test_cluster_slim_fields(tool_client):
    client, router = tool_client
    router.get(f"{V1}/clusters").mock(
        return_value=Response(200, json={"items": [fx("cluster.json")]})
    )
    out = await call(client, "argocd_list_clusters", {})
    row = out["items"][0]
    assert row["applications_count"] == 3
    assert row["cache"]["resourcesCount"] == 120
    assert row["connection"]["status"] == "Successful"


async def test_project_jwt_tokens_removed(tool_client):
    client, router = tool_client
    router.get(f"{V1}/projects/default").mock(return_value=Response(200, json=fx("project.json")))
    result = await client.call_tool("argocd_get_project", {"name": "default", "full": True})
    assert "jwtTokens" not in result.content[0].text


async def test_project_detail_slim_token_count(tool_client):
    client, router = tool_client
    router.get(f"{V1}/projects/default").mock(return_value=Response(200, json=fx("project.json")))
    out = await call(client, "argocd_get_project", {"name": "default"})
    role = out["spec"]["roles"][0]
    assert role["token_count"] == 1
    assert "jwtTokens" not in role


async def test_list_projects_slim(tool_client):
    client, router = tool_client
    router.get(f"{V1}/projects").mock(
        return_value=Response(200, json={"items": [fx("project.json")]})
    )
    out = await call(client, "argocd_list_projects", {})
    row = out["items"][0]
    assert row["name"] == "default"
    assert row["roles"] == ["ci"]
    assert row["sync_windows"] == 1


# ── repositories refs/apps ─────────────────────────────────────────


async def test_repository_refs_paged(tool_client):
    client, router = tool_client
    router.get(url__regex=rf"{V1}/repositories/.+/refs").mock(
        return_value=Response(200, json={"branches": ["main", "dev"], "tags": ["v1"]})
    )
    out = await call(client, "argocd_get_repository_refs", {"repo": "https://x.git", "limit": 1})
    assert out["branches"]["total"] == 2 and out["branches"]["has_more"] is True
    assert out["tags"]["items"] == ["v1"]


async def test_repository_apps(tool_client):
    client, router = tool_client
    router.get(url__regex=rf"{V1}/repositories/.+/apps").mock(
        return_value=Response(
            200, json={"items": [{"path": "guestbook", "type": "Helm", "extra": 1}]}
        )
    )
    out = await call(client, "argocd_list_repository_apps", {"repo": "https://x.git"})
    assert out["items"][0] == {"path": "guestbook", "type": "Helm"}


# ── server/account ─────────────────────────────────────────────────


async def test_version_slim(tool_client):
    client, router = tool_client
    router.get("/api/version").mock(return_value=Response(200, json=fx("version.json")))
    out = await call(client, "argocd_get_version", {})
    assert out["version"].startswith("v3.")
    assert out["kustomize_version"] == "v5.4.3"


async def test_userinfo(tool_client):
    client, router = tool_client
    router.get(f"{V1}/session/userinfo").mock(
        return_value=Response(
            200, json={"username": "ci", "groups": ["g"], "loggedIn": True, "iss": "argocd"}
        )
    )
    out = await call(client, "argocd_get_userinfo", {})
    assert out == {"username": "ci", "groups": ["g"], "logged_in": True, "issuer": "argocd"}


async def test_can_i_yes_no(tool_client):
    client, router = tool_client
    route = router.get(url__regex=rf"{V1}/account/can-i/.+").mock(
        return_value=Response(200, json={"value": "yes"})
    )
    out = await call(client, "argocd_can_i", {"resource": "applications", "action": "sync"})
    assert out == {
        "allowed": True,
        "resource": "applications",
        "action": "sync",
        "subresource": "*/*",
    }
    route.mock(return_value=Response(200, json={"value": "no"}))
    out = await call(client, "argocd_can_i", {"resource": "applications", "action": "sync"})
    assert out["allowed"] is False


async def test_settings_slim(tool_client):
    client, router = tool_client
    router.get(f"{V1}/settings").mock(
        return_value=Response(
            200,
            json={
                "url": "https://argocd.example.com",
                "execEnabled": True,
                "appsInAnyNamespaceEnabled": False,
                "trackingMethod": "annotation",
                "plugins": [{"name": "kustomize"}],
                "resourceOverrides": {"apps/Deployment": {}},
                "dexConfig": "secret",
            },
        )
    )
    out = await call(client, "argocd_get_settings", {})
    assert out["url"] == "https://argocd.example.com"
    assert out["exec_enabled"] is True
    assert out["plugins"] == ["kustomize"]
    assert out["resource_overrides"] == ["apps/Deployment"]
    assert "dexConfig" not in out


# ── extra coverage: full views, filters, remaining tools ───────────


async def test_settings_full_scrubbed(tool_client):
    client, router = tool_client
    router.get(f"{V1}/settings").mock(
        return_value=Response(200, json={"url": "u", "password": "SECRET", "execEnabled": True})
    )
    result = await client.call_tool("argocd_get_settings", {"full": True})
    assert "SECRET" not in result.content[0].text
    assert parse(result)["execEnabled"] is True


async def test_get_applicationset_full_and_slim(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applicationsets/guestbooks").mock(
        return_value=Response(200, json=fx("applicationset.json"))
    )
    out = await call(client, "argocd_get_applicationset", {"name": "guestbooks"})
    assert out["status"]["applicationStatus"][0]["application"] == "in-cluster-guestbook"
    full = await call(client, "argocd_get_applicationset", {"name": "guestbooks", "full": True})
    assert full["spec"]["template"]["metadata"]["name"] == "{{cluster}}-guestbook"


async def test_get_resource_full_returns_raw(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook/resource").mock(
        return_value=Response(200, json={"manifest": "{}", "extra": 1})
    )
    out = await call(
        client,
        "argocd_get_resource",
        {"name": "guestbook", "kind": "Pod", "resource_name": "p", "full": True},
    )
    assert out["extra"] == 1


async def test_list_applications_operation_and_dest_namespace_filters(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications").mock(
        return_value=Response(200, json=fx("application_list.json"))
    )
    out = await call(client, "argocd_list_applications", {"operation_phase": "Running"})
    assert [r["name"] for r in out["items"]] == ["api"]
    out = await call(client, "argocd_list_applications", {"dest_namespace": "default"})
    assert out["total"] == 3
    out = await call(client, "argocd_list_applications", {"dest_namespace": "nope"})
    assert out["total"] == 0


async def test_sync_windows(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook/syncwindows").mock(
        return_value=Response(
            200,
            json={
                "canSync": True,
                "activeWindows": [],
                "assignedWindows": [
                    {"kind": "allow", "schedule": "* * * * *", "duration": "1h", "manualSync": True}
                ],
            },
        )
    )
    out = await call(client, "argocd_get_sync_windows", {"name": "guestbook"})
    assert out["can_sync"] is True
    assert out["assigned_windows"][0]["manualSync"] is True


async def test_list_resource_actions(tool_client):
    client, router = tool_client
    route = router.get(f"{V1}/applications/guestbook/resource/actions").mock(
        return_value=Response(
            200, json={"actions": [{"name": "restart", "disabled": False, "params": []}]}
        )
    )
    out = await call(
        client,
        "argocd_list_resource_actions",
        {"name": "guestbook", "kind": "Deployment", "resource_name": "web"},
    )
    assert out["actions"][0]["name"] == "restart"
    # AR-R01: target goes in `resourceName`, not `name`
    params = dict(route.calls.last.request.url.params)
    assert params["resourceName"] == "web"
    assert "name" not in params


async def test_run_resource_action_and_delete_resource(tool_client):
    client, router = tool_client
    run_route = router.post(f"{V1}/applications/guestbook/resource/actions/v2").mock(
        return_value=Response(200, json={})
    )
    out = await call(
        client,
        "argocd_run_resource_action",
        {
            "name": "guestbook",
            "action": "restart",
            "kind": "Deployment",
            "resource_name": "web",
            "parameters": {"k": "v"},
        },
    )
    assert out["status"] == "ran"
    # AR-R02: everything goes in the JSON body (applicationResourceActionRunRequestV2),
    # with no query string — the route binds the body, so query fields are ignored.
    req = run_route.calls.last.request
    assert req.url.query == b""
    assert json.loads(req.content) == {
        "name": "guestbook",
        "resourceName": "web",
        "kind": "Deployment",
        "version": "v1",
        "group": "",
        "action": "restart",
        "resourceActionParameters": [{"name": "k", "value": "v"}],
    }
    del_route = router.delete(f"{V1}/applications/guestbook/resource").mock(
        return_value=Response(200, json={})
    )
    out = await call(
        client,
        "argocd_delete_resource",
        {"name": "guestbook", "kind": "Pod", "resource_name": "p", "force": True},
    )
    assert out["status"] == "deleted"
    # AR-R01: delete targets `resourceName`, not `name`
    del_params = dict(del_route.calls.last.request.url.params)
    assert del_params["resourceName"] == "p"
    assert "name" not in del_params


# ── AR-R03: path-segment injection is blocked before any request ───


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("argocd_delete_application", {"name": "argocd/../clusters/https%3A%2F%2Fprod"}),
        ("argocd_patch_application", {"name": "argocd/../projects/default", "patch": "{}"}),
        ("argocd_get_application", {"name": "../settings"}),
        ("argocd_get_project", {"name": "../../applications/guestbook"}),
        ("argocd_get_applicationset", {"name": "../../clusters/x"}),
        (
            "argocd_can_i",
            {"resource": "applications", "action": "get", "subresource": "../../clusters/x"},
        ),
    ],
)
async def test_path_injection_blocked(tool_client, tool, args):
    client, router = tool_client
    with pytest.raises(ToolError):
        await client.call_tool(tool, args)
    assert len(router.calls) == 0  # nothing ever reached the API


async def test_revision_is_url_encoded(tool_client):
    client, router = tool_client
    route = router.get(url__regex=rf"{V1}/applications/guestbook/revisions/.+/metadata").mock(
        return_value=Response(200, json={"author": "a"})
    )
    await call(
        client,
        "argocd_get_revision_metadata",
        {"name": "guestbook", "revision": "release/1.2"},
    )
    # the '/' in the revision is encoded so it stays one path segment
    assert "revisions/release%2F1.2/metadata" in str(route.calls.last.request.url)


# ── AR-R05: pod-log client-side caps ───────────────────────────────


async def test_pod_logs_rejects_zero_tail(tool_client):
    client, _router = tool_client
    with pytest.raises(ToolError):
        await client.call_tool("argocd_get_pod_logs", {"name": "guestbook", "tail_lines": 0})


async def test_pod_logs_caps_total_lines(tool_client):
    client, router = tool_client
    lines = [
        json.dumps({"result": {"content": f"line {i}", "podName": "p", "last": False}})
        for i in range(1500)
    ]
    lines.append(json.dumps({"result": {"last": True}}))
    router.get(f"{V1}/applications/guestbook/logs").mock(
        return_value=Response(200, text="\n".join(lines))
    )
    out = await call(client, "argocd_get_pod_logs", {"name": "guestbook", "tail_lines": 1000})
    assert out["shown_lines"] == srv.MAX_LOG_LINES == 1000
    assert out["truncated"] is True


async def test_pod_logs_caps_line_length(tool_client):
    client, router = tool_client
    big = "x" * 5000
    stream = "\n".join(
        [
            json.dumps({"result": {"content": big, "podName": "p", "last": False}}),
            json.dumps({"result": {"last": True}}),
        ]
    )
    router.get(f"{V1}/applications/guestbook/logs").mock(return_value=Response(200, text=stream))
    out = await call(client, "argocd_get_pod_logs", {"name": "guestbook"})
    assert len(out["lines"][0]["content"]) == srv.MAX_LOG_LINE_CHARS == 2000
    assert out["truncated"] is True


async def test_delete_application(tool_client):
    client, router = tool_client
    route = router.delete(f"{V1}/applications/guestbook").mock(return_value=Response(200, json={}))
    out = await call(client, "argocd_delete_application", {"name": "guestbook", "cascade": False})
    assert out == {"status": "deleted", "name": "guestbook", "cascade": False}
    assert dict(route.calls.last.request.url.params)["propagationPolicy"] == "foreground"


async def test_invalidate_cluster_cache_server_url(tool_client):
    client, router = tool_client
    route = router.post(url__regex=rf"{V1}/clusters/.+/invalidate-cache").mock(
        return_value=Response(200, json={"info": {"cacheInfo": {"resourcesCount": 0}}})
    )
    out = await call(
        client, "argocd_invalidate_cluster_cache", {"cluster": "https://k8s.default.svc"}
    )
    assert out["status"] == "invalidated"
    # server URL -> no id.type query
    assert "id.type" not in dict(route.calls.last.request.url.params)


async def test_repositories_repo_filter(tool_client):
    client, router = tool_client
    router.get(f"{V1}/repositories").mock(
        return_value=Response(200, json={"items": [fx("repository.json")]})
    )
    out = await call(client, "argocd_list_repositories", {"repo": "https://nomatch"})
    assert out["total"] == 0


async def test_managed_resources_states_and_revision_integrity(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook/revisions/abc/metadata").mock(
        return_value=Response(
            200, json={"author": "a", "sourceIntegrityResult": {"status": "verified"}}
        )
    )
    out = await call(
        client, "argocd_get_revision_metadata", {"name": "guestbook", "revision": "abc"}
    )
    assert out["source_integrity"] == "verified"


async def test_version_path_prefix_in_tool(tool_client):
    client, router = tool_client
    router.get("/api/version").mock(return_value=Response(200, json=fx("version.json")))
    out = await call(client, "argocd_get_version", {})
    assert out["git_commit"] == "abc1234def5678"


# ── branch coverage: bodies with app_namespace, filters, multi-source ──


async def test_sync_body_app_namespace_dryrun_retry(tool_client):
    client, router = tool_client
    route = router.post(f"{V1}/applications/guestbook/sync").mock(
        return_value=Response(200, json={})
    )
    await call(
        client,
        "argocd_sync_application",
        {"name": "team-a/guestbook", "project": "p", "dry_run": True, "retry_limit": 3},
    )
    body = json.loads(route.calls.last.request.content)
    assert body["appNamespace"] == "team-a" and body["project"] == "p"
    assert body["dryRun"] is True and body["retryStrategy"] == {"limit": 3}


async def test_rollback_body_app_namespace(tool_client):
    client, router = tool_client
    app = fx("application.json")
    app["spec"]["syncPolicy"] = {}
    router.get(f"{V1}/applications/guestbook").mock(return_value=Response(200, json=app))
    route = router.post(f"{V1}/applications/guestbook/rollback").mock(
        return_value=Response(200, json={})
    )
    await call(
        client,
        "argocd_rollback_application",
        {"name": "team-a/guestbook", "project": "p", "history_id": 1},
    )
    body = json.loads(route.calls.last.request.content)
    assert body["appNamespace"] == "team-a" and body["project"] == "p"


async def test_create_with_chart_dest_name_app_namespace(tool_client):
    client, router = tool_client
    route = router.post(f"{V1}/applications").mock(return_value=Response(200, json={}))
    await call(
        client,
        "argocd_create_application",
        {
            "name": "c",
            "project": "default",
            "repo_url": "https://r",
            "chart": "redis",
            "dest_name": "prod",
            "app_namespace": "team-a",
            "helm_value_files": ["values.yaml"],
            "helm_release_name": "r1",
            "kustomize_images": ["img:v2"],
            "sync_options": ["Validate=false"],
        },
    )
    body = json.loads(route.calls.last.request.content)
    assert body["spec"]["source"]["chart"] == "redis"
    assert body["spec"]["destination"] == {"name": "prod"}
    assert body["metadata"]["namespace"] == "team-a"
    assert body["spec"]["source"]["helm"]["valueFiles"] == ["values.yaml"]
    assert body["spec"]["source"]["kustomize"] == {"images": ["img:v2"]}


async def test_patch_body_app_namespace(tool_client):
    client, router = tool_client
    route = router.patch(f"{V1}/applications/guestbook").mock(return_value=Response(200, json={}))
    await call(
        client,
        "argocd_patch_application",
        {"name": "team-a/guestbook", "project": "p", "patch": "{}"},
    )
    body = json.loads(route.calls.last.request.content)
    assert body["appNamespace"] == "team-a" and body["project"] == "p"


async def test_generate_with_appset_namespace(tool_client):
    client, router = tool_client
    route = router.post(f"{V1}/applicationsets/generate").mock(
        return_value=Response(200, json={"applications": []})
    )
    await call(
        client,
        "argocd_generate_applicationset",
        {"applicationset": '{"kind":"ApplicationSet"}', "appset_namespace": "argocd"},
    )
    assert json.loads(route.calls.last.request.content)["appsetNamespace"] == "argocd"


async def test_clusters_name_and_server_filter(tool_client):
    client, router = tool_client
    router.get(f"{V1}/clusters").mock(
        return_value=Response(200, json={"items": [fx("cluster.json")]})
    )
    assert (await call(client, "argocd_list_clusters", {"name": "in-cluster"}))["total"] == 1
    assert (await call(client, "argocd_list_clusters", {"name": "nope"}))["total"] == 0
    assert (
        await call(client, "argocd_list_clusters", {"server": "https://kubernetes.default.svc"})
    )["total"] == 1


async def test_resource_tree_health_filter_and_info(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook/resource-tree").mock(
        return_value=Response(200, json=fx("resource_tree.json"))
    )
    out = await call(
        client,
        "argocd_get_resource_tree",
        {"name": "guestbook", "health_status": "Degraded", "include_info": True},
    )
    assert all((n.get("health") or {}).get("status") == "Degraded" for n in out["items"])
    assert "info" in out["items"][0]


async def test_manifests_resource_name_filter(tool_client):
    client, router = tool_client
    payload = {
        "manifests": [
            json.dumps({"kind": "Pod", "metadata": {"name": "a"}}),
            json.dumps({"kind": "Pod", "metadata": {"name": "b"}}),
        ]
    }
    router.get(f"{V1}/applications/guestbook/manifests").mock(
        return_value=Response(200, json=payload)
    )
    out = await call(client, "argocd_get_manifests", {"name": "guestbook", "resource_name": "b"})
    assert [m["metadata"]["name"] for m in out["manifests"]] == ["b"]


async def test_get_application_resources_modes(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook").mock(
        return_value=Response(200, json=fx("application.json"))
    )
    out = await call(client, "argocd_get_application", {"name": "guestbook", "resources": "all"})
    assert len(out["status"]["resources"]) == 3
    out = await call(
        client, "argocd_get_application", {"name": "guestbook", "resources": "out_of_sync"}
    )
    assert [r["kind"] for r in out["status"]["resources"]] == ["Service"]
    out = await call(
        client, "argocd_get_application", {"name": "guestbook", "resources": "unhealthy"}
    )
    assert [r["kind"] for r in out["status"]["resources"]] == ["Pod"]


async def test_multi_source_app_and_history_revisions(tool_client):
    client, router = tool_client
    app = fx("application.json")
    app["spec"].pop("source")
    app["spec"]["sources"] = [{"repoURL": "r1", "path": "a"}, {"repoURL": "r2", "chart": "c"}]
    app["status"]["history"] = [
        {
            "id": 5,
            "revisions": ["r1a", "r2a"],
            "sources": [{"repoURL": "r1", "path": "a"}],
            "initiatedBy": {},
        }
    ]
    app["status"]["operationState"]["operation"]["sync"]["resources"] = [
        {"kind": "Deployment", "name": "web"}
    ]
    router.get(f"{V1}/applications").mock(return_value=Response(200, json={"items": [app]}))
    out = await call(client, "argocd_list_applications", {})
    assert "sources" in out["items"][0]
    router.get(f"{V1}/applications/guestbook").mock(return_value=Response(200, json=app))
    hist = await call(client, "argocd_get_application_history", {"name": "guestbook"})
    assert hist["items"][0]["revisions"] == ["r1a", "r2a"]
    assert "sources" in hist["items"][0]
    op = await call(client, "argocd_get_operation", {"name": "guestbook"})
    assert op["sync"]["resources"] == [{"kind": "Deployment", "name": "web"}]


async def test_get_resource_non_json_manifest(tool_client):
    client, router = tool_client
    router.get(f"{V1}/applications/guestbook/resource").mock(
        return_value=Response(200, json={"manifest": "not-json"})
    )
    out = await call(
        client, "argocd_get_resource", {"name": "guestbook", "kind": "Pod", "resource_name": "p"}
    )
    assert out["manifest"] == "not-json"
