import csv
import hashlib
import hmac
import io

import pytest
import httpx
from fastapi.testclient import TestClient

from app.business import GenerationStateMachine, InvalidTaskTransition
from app.domain import GenerationRun, GenerationStatus, TaskLifecycleStatus
from app.main import create_app
from app.providers import OpenAICompatibleMediaProvider
from app.store import VersionConflict


AUTH_SECRET = "test-identity-secret"


def signed(organization_id, user_id):
    signature = hmac.new(AUTH_SECRET.encode(), f"{organization_id}:{user_id}".encode(), hashlib.sha256).hexdigest()
    return {"X-Organization-ID": organization_id, "X-User-ID": user_id, "X-Identity-Signature": signature}


OWNER = signed("org_demo", "user_owner")
REVIEWER = signed("org_demo", "user_reviewer")
VIEWER = signed("org_demo", "user_viewer")
OTHER = signed("org_other", "user_other")


def create_project(client, payload, headers=OWNER):
    response = client.post("/api/projects", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_strict_auth_refuses_to_start_without_gateway_secret(checkpoint_dir, tmp_path):
    with pytest.raises(ValueError, match="IDENTITY_HMAC_SECRET"):
        create_app(
            checkpoint_dir=checkpoint_dir,
            media_root=tmp_path,
            seed_demo=False,
            environment={"MANGAFLOW_PROVIDER": "mock", "MANGAFLOW_AUTH_MODE": "strict"},
        )


def test_strict_auth_rbac_and_tenant_isolation(checkpoint_dir, tmp_path, mars_payload):
    app = create_app(
        checkpoint_dir=checkpoint_dir,
        media_root=tmp_path,
        seed_demo=False,
        environment={"MANGAFLOW_PROVIDER": "mock", "MANGAFLOW_AUTH_MODE": "strict", "MANGAFLOW_IDENTITY_HMAC_SECRET": AUTH_SECRET},
    )
    with TestClient(app) as client:
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/projects", headers=signed("org_demo", "unknown")).status_code == 401
        assert client.get("/api/projects", headers={"X-Organization-ID": "org_demo", "X-User-ID": "user_owner", "X-Identity-Signature": "forged"}).status_code == 401
        project = create_project(client, mars_payload)

        assert client.get(f"/api/projects/{project['id']}", headers=REVIEWER).status_code == 200
        assert client.post("/api/projects", json=mars_payload, headers=REVIEWER).status_code == 403
        assert client.post(
            f"/api/projects/{project['id']}/comments",
            json={"target_type": "shot", "target_id": "shot_01", "body": "请复核第一帧"},
            headers=VIEWER,
        ).status_code == 403

        # Cross-tenant reads intentionally look like a missing resource.
        assert client.get(f"/api/projects/{project['id']}", headers=OTHER).status_code == 404
        assert client.get("/api/projects", headers=OTHER).json() == []


def test_server_membership_controls_role_not_client_header(checkpoint_dir, tmp_path):
    app = create_app(
        checkpoint_dir=checkpoint_dir,
        media_root=tmp_path,
        seed_demo=False,
        environment={"MANGAFLOW_PROVIDER": "mock", "MANGAFLOW_AUTH_MODE": "strict", "MANGAFLOW_IDENTITY_HMAC_SECRET": AUTH_SECRET},
    )
    with TestClient(app) as client:
        response = client.get("/api/me", headers={**VIEWER, "X-Role": "owner"})
        assert response.status_code == 200
        assert response.json()["role"] == "viewer"


def test_asset_version_invalidates_dependent_shot(client, mars_payload):
    project = create_project(client, mars_payload)
    base = f"/api/projects/{project['id']}"
    first = client.post(f"{base}/assets/hero_design/versions", json={
        "name": "主角设定 v1", "content_hash": "12345678abcd", "license_name": "owned"
    })
    assert first.status_code == 201
    dependency = client.post(f"{base}/dependencies", json={
        "upstream_asset_id": "hero_design", "downstream_asset_id": "shot_01", "relation": "visual_reference"
    })
    assert dependency.status_code == 201
    second = client.post(f"{base}/assets/hero_design/versions", json={
        "name": "主角设定 v2", "content_hash": "abcdef123456", "license_name": "owned"
    })
    assert second.status_code == 201
    assert second.json()["asset"]["version"] == 2
    assert second.json()["stale_artifacts"] == ["shot_01"]
    stored = client.get(base).json()
    shot = next(item for item in stored["shots"] if item["id"] == "shot_01")
    assert shot["status"] == "stale"
    assert "advanced to v2" in shot["stale_reasons"][0]
    assert any(item["action"] == "asset.version_created" for item in stored["audit_logs"])


def test_budget_cap_blocks_submission_before_provider_charge(client, mars_payload):
    project = create_project(client, {**mars_payload, "budget_limit": 0.05})
    base = f"/api/projects/{project['id']}"
    assert client.post(f"{base}/approve-assets").status_code == 200
    assert client.post(f"{base}/workflow", json={"command": "start"}).status_code == 200
    response = client.post(f"{base}/shots/shot_01/generate")
    assert response.status_code == 402
    assert response.json()["detail"]["estimated_cost"] == 0.08
    assert client.get(base).json()["generation_runs"] == []


def test_generation_state_machine_records_timeline_and_rejects_regression():
    run = GenerationRun(
        id="run_1", shot_id="shot_1", provider="mock", provider_task_id="task_1",
        idempotency_key="key", status=GenerationStatus.queued, recipe_id="recipe_1",
    )
    machine = GenerationStateMachine()
    machine.transition(run, TaskLifecycleStatus.queued, "accepted")
    machine.transition(run, TaskLifecycleStatus.running, "worker started")
    machine.transition(run, TaskLifecycleStatus.succeeded, "artifact stored")
    assert run.status == GenerationStatus.completed
    assert [event.to_status for event in run.timeline] == [
        TaskLifecycleStatus.queued, TaskLifecycleStatus.running, TaskLifecycleStatus.succeeded,
    ]
    with pytest.raises(InvalidTaskTransition):
        machine.transition(run, TaskLifecycleStatus.running)


def test_storyboard_csv_round_trip_and_optimistic_conflict(client, mars_payload):
    project = create_project(client, mars_payload)
    base = f"/api/projects/{project['id']}"
    exported = client.get(f"{base}/storyboard.csv")
    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    rows[0]["title"] = "新的开场标题"
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

    imported = client.post(f"{base}/storyboard/import", json={
        "csv_text": output.getvalue(), "expected_project_version": project["version"],
    })
    assert imported.status_code == 200
    assert imported.json()["project_version"] == project["version"] + 1
    assert client.get(base).json()["shots"][0]["title"] == "新的开场标题"
    conflict = client.post(f"{base}/storyboard/import", json={
        "csv_text": output.getvalue(), "expected_project_version": project["version"],
    })
    assert conflict.status_code == 409


def test_repository_compare_and_swap_rejects_lost_update(client, mars_payload):
    project = create_project(client, mars_payload)
    store = client.app.state.store
    first = store.snapshot(project["id"], "org_demo")
    second = store.snapshot(project["id"], "org_demo")
    first.name = "producer A"
    store.replace(first, expected_version=first.version)
    second.name = "producer B"
    with pytest.raises(VersionConflict):
        store.replace(second, expected_version=second.version)


def test_comments_metrics_audit_and_manifest_are_traceable(client, mars_payload):
    project = create_project(client, mars_payload)
    base = f"/api/projects/{project['id']}"
    comment = client.post(f"{base}/comments", json={
        "target_type": "shot", "target_id": "shot_01", "body": "构图通过，保留道具状态"
    }, headers=REVIEWER)
    assert comment.status_code == 201
    metrics = client.get(f"{base}/metrics").json()
    assert metrics["metric_source"] == "operational-records"
    assert metrics["budget_limit"] == 8
    manifest = client.post(f"{base}/export").json()
    assert manifest["assembled"] is False
    assert manifest["manifest"]["status"] == "blocked"
    assert len(manifest["manifest"]["blockers"]) == len(project["shots"])
    logs = client.get(f"{base}/audit-logs").json()
    assert {item["action"] for item in logs} >= {
        "project.created", "comment.created", "delivery.manifest_created",
    }


def test_non_unknown_run_reconciliation_is_idempotent(client, mars_payload):
    project = create_project(client, mars_payload)
    base = f"/api/projects/{project['id']}"
    client.post(f"{base}/approve-assets")
    client.post(f"{base}/workflow", json={"command": "start"})
    run = client.post(f"{base}/shots/shot_01/generate").json()["run"]
    response = client.post(f"{base}/runs/{run['id']}/reconcile")
    assert response.status_code == 200
    assert response.json()["reconciled"] is False


def test_submission_timeout_becomes_unknown_then_reconciles_by_idempotency(checkpoint_dir, tmp_path, mars_payload):
    recovery_calls = 0

    def handler(request: httpx.Request):
        nonlocal recovery_calls
        if request.method == "POST":
            raise httpx.ReadTimeout("submission response lost", request=request)
        if request.url.path.endswith("/by-idempotency/" + request.url.path.rsplit("/", 1)[-1]):
            recovery_calls += 1
            if recovery_calls == 1:
                raise httpx.ReadTimeout("recovery lookup also timed out", request=request)
            return httpx.Response(200, json={"id": "recovered-task", "status": "running"})
        return httpx.Response(404, json={"error": "missing"})

    provider = OpenAICompatibleMediaProvider(
        "https://provider.test", "secret", "video", httpx.MockTransport(handler),
    )
    app = create_app(
        checkpoint_dir=checkpoint_dir,
        media_root=tmp_path,
        seed_demo=False,
        environment={"MANGAFLOW_PROVIDER": "mock"},
        provider_override=("cloud-video", provider),
    )
    with TestClient(app) as client:
        project = create_project(client, mars_payload)
        base = f"/api/projects/{project['id']}"
        client.post(f"{base}/approve-assets")
        client.post(f"{base}/workflow", json={"command": "start"})
        submitted = client.post(f"{base}/shots/shot_01/generate")
        assert submitted.status_code == 202
        body = submitted.json()
        assert body["reconciliation_required"] is True
        assert body["run"]["lifecycle_status"] == "unknown"
        assert body["run"]["error_code"] == "PROVIDER_OUTCOME_UNKNOWN"

        reconciled = client.post(f"{base}/runs/{body['run']['id']}/reconcile")
        assert reconciled.status_code == 200, reconciled.text
        run = reconciled.json()["run"]
        assert reconciled.json()["reconciled"] is True
        assert run["provider_task_id"] == "recovered-task"
        assert run["lifecycle_status"] == "running"
        assert [item["to_status"] for item in run["timeline"]] == ["submitting", "unknown", "running"]
