from app.adaptation import AdaptationAgent
from app.demo import build_demo_project
from app.domain import CreateProjectRequest
from app.services import ContinuityEngine, PromptCompiler, RenderRouter
from app.domain import RouteRequest


def test_demo_graph_is_valid_project_wide():
    reports = ContinuityEngine().check_project(build_demo_project())
    assert all(report.passed for report in reports)


def test_project_graph_detects_unknown_scene_appearance_order_and_cycle():
    project = build_demo_project()
    project.shots[2].scene_id = "missing_scene"
    project.shots[3].character_versions = ["missing_outfit"]
    project.shots[4].previous_shot_id = "shot_08"
    project.shots[7].previous_shot_id = "shot_05"
    reports = ContinuityEngine().check_project(project)
    codes = {issue.code for report in reports for issue in report.issues}
    assert "UNKNOWN_SCENE" in codes
    assert "UNKNOWN_APPEARANCE_VERSION" in codes
    assert "INVALID_PREVIOUS_ORDER" in codes
    assert "DEPENDENCY_CYCLE" in codes


def test_must_preserve_promotes_prop_change_to_blocking_error():
    project = build_demo_project()
    current, previous = project.shots[1], project.shots[0]
    current.start_state.holding = []
    report = ContinuityEngine().check(current, previous)
    issue = next(issue for issue in report.issues if issue.field == "holding")
    assert issue.severity == "error"
    assert not report.passed


def test_adaptation_outputs_differ_for_unrelated_sources():
    agent = AdaptationAgent()
    mars = agent.run(CreateProjectRequest(name="Mars", source_text="火星机器人在红色沙暴里持续发送求救信号，维修工程师必须穿过废弃基地找到它。"))
    forest = agent.run(CreateProjectRequest(name="Forest", source_text="森林巡护员在古树旁听见迷路少年的呼救，她循着木制护符走向雾崖深处。"))
    assert mars.episode_title != forest.episode_title
    assert mars.characters[0].name != forest.characters[0].name
    assert mars.scenes[0].location != forest.scenes[0].location


def test_prompt_compiler_contains_locked_asset_and_scene_facts():
    project = build_demo_project()
    shot = project.shots[0]
    decision = RenderRouter().decide(RouteRequest(shot_type=shot.shot_type, motion_level=0))
    recipe = PromptCompiler().compile(shot, decision, project)
    assert "ivory shirt" in recipe.prompt
    assert project.scenes[0].style in recipe.prompt
    assert recipe.template_version == "mangaflow-v2"

