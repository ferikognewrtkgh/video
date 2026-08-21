from pathlib import Path

from evaluation.run_evaluation import evaluate


def test_20_shot_offline_evaluation_executes_with_perfect_fixture_match():
    dataset = Path(__file__).parents[2] / "evaluation" / "shots.json"
    result = evaluate(dataset)
    assert result["cases"] == 20
    assert result["route_accuracy"] == 1.0
    assert result["continuity_exact_match"] == 1.0
    assert result["issue_precision"] == 1.0
    assert result["issue_recall"] == 1.0
    assert result["failures"] == []

