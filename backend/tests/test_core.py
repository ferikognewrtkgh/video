from app.demo import build_demo_project
from app.domain import MetricScores, QAEvaluateRequest, RouteRequest
from app.main import check_all_continuity, generate_shot, get_project, health
from app.services import ContinuityEngine, MockProvider, PromptCompiler, QualityEngine, RenderRouter


def test_demo_project_matches_mvp_contract():
    project = build_demo_project()
    assert 6 <= len(project.shots) <= 10
    assert 45 <= sum(shot.duration_sec for shot in project.shots) <= 60
    assert len(project.characters) == 2
    assert 2 <= len(project.scenes) <= 3


def test_continuity_graph_has_no_blocking_issues():
    project = build_demo_project()
    engine = ContinuityEngine()
    reports = []
    for shot in project.shots:
        previous = next((item for item in project.shots if item.id == shot.previous_shot_id), None)
        reports.append(engine.check(shot, previous))
    assert all(report.passed for report in reports)


def test_continuity_detects_unexplained_outfit_change():
    project = build_demo_project()
    previous, shot = project.shots[1], project.shots[2]
    shot.start_state.appearance_version = "unexpected_red_coat"
    report = ContinuityEngine().check(shot, previous)
    assert not report.passed
    assert report.issues[0].code == "UNEXPLAINED_APPEARANCE_VERSION_CHANGE"


def test_router_uses_portrait_drive_for_dialogue():
    result = RenderRouter().decide(RouteRequest(shot_type="dialogue_closeup", characters=1, motion_level=1))
    assert result.route == "portrait_drive"
    assert result.estimated_cost < 0.5


def test_router_reserves_premium_for_complex_motion():
    result = RenderRouter().decide(RouteRequest(shot_type="premium_action", characters=2, motion_level=3, camera_complexity=3, quality_priority=.9, max_cost=5))
    assert result.route == "premium_i2v"
    assert result.candidate_count == 3


def test_mock_provider_is_idempotent():
    shot = build_demo_project().shots[0]
    decision = RenderRouter().decide(RouteRequest(shot_type=shot.shot_type, motion_level=0))
    recipe = PromptCompiler().compile(shot, decision)
    provider = MockProvider()
    first = provider.submit(shot, recipe)
    second = provider.submit(shot, recipe)
    assert first.provider_task_id == second.provider_task_id


def test_quality_weighting_and_repair_strategy():
    report = QualityEngine().evaluate("shot_test", QAEvaluateRequest(metrics=MetricScores(identity=.6, prompt_alignment=.8, temporal_stability=.5, motion=.7, aesthetics=.9), failures=["identity_drift", "flicker"]), attempt=1)
    assert report.score == 68.0
    assert "参考图" in report.repair_strategy
    assert "2.5D" in report.repair_strategy


def test_human_review_after_two_failed_repairs():
    report = QualityEngine().evaluate("shot_test", QAEvaluateRequest(hard_gate_passed=False, metrics=MetricScores(identity=.9, prompt_alignment=.9, temporal_stability=.9, motion=.9, aesthetics=.9)), attempt=2)
    assert report.needs_human_review


def test_health_endpoint():
    assert health()["provider"] == "mock"


def test_project_and_continuity_endpoints():
    project = get_project("project_afterimage")
    report = check_all_continuity("project_afterimage")
    assert project.id == "project_afterimage"
    assert report["passed"] is True


def test_generate_single_shot_returns_traceable_recipe():
    payload = generate_shot("project_afterimage", "shot_08")
    assert payload["run"].idempotency_key
    assert payload["recipe"].input_hash
    assert payload["recipe"].template_version == "mangaflow-v1"
