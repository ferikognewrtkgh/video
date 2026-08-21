from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.app.domain import ContinuityState, RouteRequest, ShotSpec
from backend.app.services import ContinuityEngine, RenderRouter


def evaluate(dataset_path: Path) -> dict[str, Any]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    router, continuity = RenderRouter(), ContinuityEngine()
    route_matches = 0
    true_positive = false_positive = false_negative = 0
    exact_issue_matches = 0
    failures = []
    for index, case in enumerate(cases):
        decision = router.decide(RouteRequest.model_validate(case["route_request"]))
        route_ok = decision.route == case["expected_route"]
        route_matches += int(route_ok)
        previous = _shot("previous", 1, ContinuityState.model_validate(case["previous_state"]), None, [], [])
        current = _shot(
            case["id"], 2, ContinuityState.model_validate(case["current_state"]), "previous",
            case.get("allowed_changes", []), case.get("must_preserve", []),
        )
        report = continuity.check(current, previous)
        actual = {issue.code for issue in report.issues}
        expected = set(case["expected_issue_codes"])
        true_positive += len(actual & expected)
        false_positive += len(actual - expected)
        false_negative += len(expected - actual)
        exact_issue_matches += int(actual == expected)
        if not route_ok or actual != expected:
            failures.append({"id": case["id"], "expected_route": case["expected_route"], "actual_route": decision.route, "expected_issues": sorted(expected), "actual_issues": sorted(actual)})
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    return {
        "cases": len(cases), "route_accuracy": round(route_matches / len(cases), 4),
        "continuity_exact_match": round(exact_issue_matches / len(cases), 4),
        "issue_precision": round(precision, 4), "issue_recall": round(recall, 4),
        "failures": failures,
    }


def _shot(shot_id: str, order: int, state: ContinuityState, previous: str | None, allowed: list[str], preserved: list[str]) -> ShotSpec:
    return ShotSpec(
        id=shot_id, scene_id=state.location, order=order, title=shot_id, duration_sec=4,
        shot_type="evaluation", shot_size="medium", camera_motion="locked",
        start_state=state, end_state=state, action="evaluation", previous_shot_id=previous,
        allowed_changes=allowed, must_preserve=preserved,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("shots.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-route-accuracy", type=float, default=1.0)
    args = parser.parse_args()
    result = evaluate(args.dataset)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    return int(result["route_accuracy"] < args.min_route_accuracy or result["continuity_exact_match"] < 1.0)


if __name__ == "__main__":
    raise SystemExit(main())

