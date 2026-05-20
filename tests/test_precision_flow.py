from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, Path(rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


evaluate_tool = _load_module("evaluate_tool", "castflow/tools/eval.py")
versioning_tool = _load_module("versioning_tool", "castflow/tools/versioning.py")


def test_evaluate_mape_basic():
    result = evaluate_tool.evaluate_mape.invoke({"predictions": [110, 90], "actuals": [100, 100]})
    assert result["mape"] == 10.0
    assert result["mae"] == 10.0
    assert round(result["rmse"], 6) == 10.0
    assert result["bias"] == 0.0
    assert result["valid_n"] == 2
    assert len(result["per_point_errors"]) == 2


def test_evaluate_mape_handles_zero_actual():
    result = evaluate_tool.evaluate_mape.invoke({"predictions": [1], "actuals": [0]})
    assert result["error"] == "all actuals are zero"
    assert result["zero_actual_count"] == 1


def test_should_accept_candidate_threshold():
    accepted, reason = versioning_tool.should_accept_candidate(9.8, 10.0, True)
    assert accepted is True
    assert "improved" in reason

    rejected, reason = versioning_tool.should_accept_candidate(9.95, 10.0, True)
    assert rejected is False
    assert "below threshold" in reason


def test_summarize_diagnostics_limits_points():
    diagnostics = {
        "mape": 10,
        "mae": 1,
        "rmse": 2,
        "bias": 0.5,
        "mpe": 3,
        "zero_actual_count": 0,
        "per_point_errors": [
            {"abs_error": 1},
            {"abs_error": 5},
            {"abs_error": 3},
        ],
    }
    summary = versioning_tool.summarize_diagnostics(diagnostics, max_points=2)
    assert summary["mape"] == 10
    assert len(summary["worst_points"]) == 2
    assert summary["worst_points"][0]["abs_error"] == 5
