"""Tests for the standard CLI response envelope contract."""

from __future__ import annotations

import json

from gpt_trader.cli.response import CliErrorCode, CliResponse


class TestEnvelopeContract:
    """Test the standard envelope contract for AI agents."""

    def test_success_envelope_has_required_fields(self):
        response = CliResponse.success_response(
            command="ideas list",
            data={"runs": []},
        )
        envelope = response.to_dict()

        assert "success" in envelope
        assert "exit_code" in envelope
        assert "command" in envelope
        assert "data" in envelope
        assert "errors" in envelope
        assert "warnings" in envelope
        assert "metadata" in envelope

        assert "timestamp" in envelope["metadata"]
        assert "was_noop" in envelope["metadata"]
        assert "version" in envelope["metadata"]

    def test_error_envelope_has_required_fields(self):
        response = CliResponse.error_response(
            command="optimize view",
            code=CliErrorCode.RUN_NOT_FOUND,
            message="Not found",
        )
        envelope = response.to_dict()

        assert "success" in envelope
        assert "exit_code" in envelope
        assert "command" in envelope
        assert "data" in envelope
        assert "errors" in envelope
        assert "warnings" in envelope
        assert "metadata" in envelope

        assert envelope["errors"][0]["code"]
        assert envelope["errors"][0]["message"]

    def test_envelope_is_json_serializable(self):
        response = CliResponse.success_response(
            command="test",
            data={"nested": {"value": 123}},
        )
        json_str = response.to_json()
        parsed = json.loads(json_str)
        assert parsed["data"]["nested"]["value"] == 123
