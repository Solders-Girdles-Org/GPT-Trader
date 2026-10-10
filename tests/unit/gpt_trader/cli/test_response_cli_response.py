"""Unit tests for CliResponse dataclass."""

from __future__ import annotations

import json

from gpt_trader.cli.response import CliError, CliErrorCode, CliResponse


class TestCliResponse:
    """Test CliResponse dataclass."""

    def test_success_response_basic(self):
        response = CliResponse.success_response(
            command="ideas list",
            data={"runs": []},
        )
        assert response.success is True
        assert response.exit_code == 0
        assert response.command == "ideas list"
        assert response.data == {"runs": []}
        assert response.errors == []
        assert response.warnings == []

    def test_success_response_with_warnings(self):
        response = CliResponse.success_response(
            command="optimize view",
            data={"run_id": "opt_123"},
            warnings=["No feasible trials"],
        )
        assert response.success is True
        assert response.warnings == ["No feasible trials"]

    def test_success_response_with_noop(self):
        response = CliResponse.success_response(
            command="optimize run",
            data={"preview": True},
            was_noop=True,
        )
        assert response.was_noop is True

    def test_error_response_basic(self):
        response = CliResponse.error_response(
            command="optimize view",
            code=CliErrorCode.RUN_NOT_FOUND,
            message="Run not found",
        )
        assert response.success is False
        assert response.exit_code == 1
        assert response.data is None
        assert len(response.errors) == 1
        assert response.errors[0].code == "RUN_NOT_FOUND"

    def test_error_response_with_details(self):
        response = CliResponse.error_response(
            command="optimize view",
            code=CliErrorCode.RUN_NOT_FOUND,
            message="Run 'opt_123' not found",
            details={"run_id": "opt_123"},
        )
        assert response.errors[0].details["run_id"] == "opt_123"

    def test_to_dict_success(self):
        response = CliResponse.success_response(
            command="ideas list",
            data={"runs": [{"id": 1}]},
        )
        result = response.to_dict()

        assert result["success"] is True
        assert result["exit_code"] == 0
        assert result["command"] == "ideas list"
        assert result["data"] == {"runs": [{"id": 1}]}
        assert result["errors"] == []
        assert result["warnings"] == []
        assert "metadata" in result
        assert "timestamp" in result["metadata"]
        assert result["metadata"]["was_noop"] is False
        assert result["metadata"]["version"] == "1.0"

    def test_to_dict_error(self):
        response = CliResponse.error_response(
            command="optimize view",
            code=CliErrorCode.RUN_NOT_FOUND,
            message="Run not found",
        )
        result = response.to_dict()

        assert result["success"] is False
        assert result["exit_code"] == 1
        assert result["data"] is None
        assert len(result["errors"]) == 1
        assert result["errors"][0]["code"] == "RUN_NOT_FOUND"

    def test_to_json_compact(self):
        response = CliResponse.success_response(
            command="ideas list",
            data={"runs": []},
        )
        compact = response.to_json(compact=True)
        normal = response.to_json(compact=False)

        assert "\n" not in compact
        assert "\n" in normal
        assert json.loads(compact) == json.loads(normal)

    def test_add_warning_fluent(self):
        response = (
            CliResponse.success_response(command="test")
            .add_warning("Warning 1")
            .add_warning("Warning 2")
        )
        assert response.warnings == ["Warning 1", "Warning 2"]

    def test_add_error_fluent(self):
        error = CliError.from_code(CliErrorCode.VALIDATION_ERROR, "Invalid input")
        response = CliResponse.success_response(command="test").add_error(error)

        assert response.success is False
        assert response.exit_code == 1
        assert len(response.errors) == 1

    def test_exit_code_auto_set_on_failure(self):
        response = CliResponse(
            success=False,
            command="test",
        )
        assert response.exit_code == 1
