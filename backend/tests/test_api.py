from fastapi.testclient import TestClient

from app.main import create_app


def _create(client, payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _ready_and_running(client, project_id):
    assert client.post(f"/api/projects/{project_id}/approve-assets").status_code == 200
    assert client.post(f"/api/projects/{project_id}/workflow", json={"command": "start"}).status_code == 200


def test_real_http_create_uses_source_text(client, mars_payload):
    mars = _create(client, mars_payload)
    sea = _create(client, {
        "name": "深蓝回声",
        "source_text": "深海潜艇的声呐员听见失踪潜航员从沉船里发出的回声。她决定潜入海沟，在水压摧毁舱体前寻找真相。",
        "target_duration_sec": 50,
    })
    assert mars["episode_title"] != sea["episode_title"]
    assert {c["name"] for c in mars["characters"]} != {c["name"] for c in sea["characters"]}
    assert {e["title"] for e in mars["events"]} != {e["title"] for e in sea["events"]}
    assert mars["shots"][0]["action"] != sea["shots"][0]["action"]
    assert mars["data_mode"] == "generated"
    assert [node["node"] for node in mars["agent_trace"]] == ["adaptation", "asset_extraction", "director", "continuity"]


def test_http_validation_404_and_cors(client):
    invalid = client.post("/api/projects", json={"name": "x", "source_text": "too short"})
    assert invalid.status_code == 422
    assert client.get("/api/projects/missing").status_code == 404
    cors = client.options("/api/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert cors.status_code == 200
    assert cors.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_quality_metrics_reject_out_of_range_values(client):
    response = client.post("/api/projects/missing/shots/missing/qa", json={
        "metrics": {"identity": 99, "prompt_alignment": 99, "temporal_stability": 99, "motion": 99, "aesthetics": 99}
    })
    assert response.status_code == 422
    assert len(response.json()["detail"]) == 5


def test_continuity_conflict_blocks_generation(client, mars_payload):
    project = _create(client, mars_payload)
    _ready_and_running(client, project["id"])
    stored = client.app.state.store.get(project["id"])
    stored.shots[1].start_state.appearance_version = "unapproved_red_coat"
    response = client.post(f"/api/projects/{project['id']}/shots/shot_02/generate")
    assert response.status_code == 409
    assert response.json()["detail"]["report"]["passed"] is False


def test_cancel_propagates_and_prevents_new_generation(client, mars_payload):
    project = _create(client, mars_payload)
    _ready_and_running(client, project["id"])
    generated = client.post(f"/api/projects/{project['id']}/shots/shot_01/generate")
    assert generated.status_code == 202
    run_id = generated.json()["run"]["id"]
    cancelled = client.post(f"/api/projects/{project['id']}/workflow", json={"command": "cancel"})
    assert cancelled.status_code == 200
    run = next(item for item in cancelled.json()["generation_runs"] if item["id"] == run_id)
    assert run["status"] == "cancelled"
    assert client.post(f"/api/projects/{project['id']}/shots/shot_02/generate").status_code == 409
    assert client.post(f"/api/projects/{project['id']}/runs/{run_id}/tick").status_code == 409


def test_duplicate_submit_and_tick_do_not_duplicate_cost(client, mars_payload):
    project = _create(client, mars_payload)
    _ready_and_running(client, project["id"])
    path = f"/api/projects/{project['id']}/shots/shot_01/generate"
    first, second = client.post(path), client.post(path)
    assert first.status_code == second.status_code == 202
    assert first.json()["run"]["provider_task_id"] == second.json()["run"]["provider_task_id"]
    assert second.json()["deduplicated"] is True
    run_id = first.json()["run"]["id"]
    for _ in range(5):
        client.post(f"/api/projects/{project['id']}/runs/{run_id}/tick")
    stored = client.get(f"/api/projects/{project['id']}").json()
    assert len(stored["generation_runs"]) == 1
    assert len([event for event in stored["cost_events"] if event["shot_id"] == "shot_01"]) == 1


def test_restart_recovers_project_and_idempotent_run(checkpoint_dir, tmp_path, mars_payload):
    first_app = create_app(checkpoint_dir, tmp_path, seed_demo=False, environment={"MANGAFLOW_PROVIDER": "mock"})
    with TestClient(first_app) as first:
        project = _create(first, mars_payload)
        _ready_and_running(first, project["id"])
        generated = first.post(f"/api/projects/{project['id']}/shots/shot_01/generate").json()
    second_app = create_app(checkpoint_dir, tmp_path, seed_demo=False, environment={"MANGAFLOW_PROVIDER": "mock"})
    with TestClient(second_app) as second:
        recovered = second.get(f"/api/projects/{project['id']}")
        assert recovered.status_code == 200
        assert recovered.json()["workflow_status"] == "running"
        duplicate = second.post(f"/api/projects/{project['id']}/shots/shot_01/generate")
        assert duplicate.status_code == 202
        assert duplicate.json()["deduplicated"] is True
        assert duplicate.json()["run"]["provider_task_id"] == generated["run"]["provider_task_id"]
        assert second.get("/api/health").json()["recovered_projects"] == 1


def test_keyframe_approval_quality_run_and_export_are_explicit(client, mars_payload):
    project = _create(client, mars_payload)
    first = client.post(f"/api/projects/{project['id']}/shots/shot_01/approve-keyframe")
    second = client.post(f"/api/projects/{project['id']}/shots/shot_01/approve-keyframe")
    assert first.status_code == second.status_code == 200
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    quality = client.post(f"/api/projects/{project['id']}/quality-run").json()
    assert quality["status"] == "no_media"
    assert quality["metric_source"] == "decoded-media"
    export = client.post(f"/api/projects/{project['id']}/export").json()
    assert export["assembled"] is False
    assert export["status"] == "manifest_only"
