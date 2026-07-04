from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from scripts.agents import agent_artifacts


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str = "generated\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _resource(path: str, files: list[str], generator: str) -> dict[str, object]:
    return {
        "path": path,
        "description": f"{path} artifacts",
        "files": files,
        "generator": generator,
    }


def _create_valid_agent_artifacts(base: Path) -> Path:
    source = base / "var" / "agents"
    resources = {
        "schemas": _resource(
            "schemas/",
            ["bot_config_schema.json", "risk_config_schema.json"],
            "scripts/agents/generate_config_schemas.py",
        ),
        "models": _resource(
            "models/",
            ["interfaces_schema.json", "enums_schema.json", "errors_schema.json"],
            "scripts/agents/export_model_schemas.py",
        ),
        "logging": _resource(
            "logging/",
            ["event_catalog.json", "log_schema.json"],
            "scripts/agents/generate_event_catalog.py",
        ),
        "observability": _resource(
            "observability/",
            ["metrics_catalog.json", "metrics_catalog.md"],
            "scripts/agents/generate_metrics_catalog.py",
        ),
        "configuration": _resource(
            "configuration/",
            ["environment_variables.json", "environment_variables.md"],
            "scripts/agents/generate_environment_variable_reference.py",
        ),
        "testing": _resource(
            "testing/",
            ["test_inventory.json", "markers.json"],
            "scripts/agents/generate_test_inventory.py",
        ),
        "validation": _resource(
            "validation/",
            ["validator_registry.json", "rules_registry.json"],
            "scripts/agents/generate_validator_registry.py",
        ),
        "broker": _resource(
            "broker/",
            ["api_reference.json", "examples.json"],
            "scripts/agents/generate_broker_api_docs.py",
        ),
        "reasoning": _resource(
            "reasoning/",
            ["cli_flow_map.json", "cli_flow_map.md"],
            "scripts/agents/generate_reasoning_artifacts.py",
        ),
        "health": _resource(
            "health/",
            ["agent_health_schema.json", "agent_health_example.json"],
            "scripts/agents/generate_agent_health_schema.py",
        ),
    }
    _write_json(source / "index.json", {"version": "1.2", "resources": resources})

    _write_json(
        source / "schemas" / "index.json",
        {"files": {"bot_config": "bot_config_schema.json"}},
    )
    _write_json(source / "schemas" / "bot_config_schema.json", {"type": "object"})
    _write_json(source / "schemas" / "risk_config_schema.json", {"type": "object"})
    _write_json(
        source / "models" / "index.json", {"files": {"interfaces": "interfaces_schema.json"}}
    )
    _write_json(source / "models" / "interfaces_schema.json", {"interfaces": []})
    _write_json(source / "models" / "enums_schema.json", {"enums": []})
    _write_json(source / "models" / "errors_schema.json", {"errors": []})
    _write_json(source / "logging" / "event_catalog.json", {"events": ["runtime_start"]})
    _write_json(source / "logging" / "log_schema.json", {"type": "object"})
    _write_json(
        source / "logging" / "index.json", {"files": {"event_catalog": "event_catalog.json"}}
    )
    _write_json(
        source / "observability" / "metrics_catalog.json", {"metrics": [{"name": "metric"}]}
    )
    _write_text(source / "observability" / "metrics_catalog.md")
    _write_json(
        source / "observability" / "index.json", {"files": {"metrics": "metrics_catalog.json"}}
    )
    _write_json(
        source / "configuration" / "environment_variables.json", {"variables": [{"name": "BROKER"}]}
    )
    _write_text(source / "configuration" / "environment_variables.md")
    _write_json(
        source / "configuration" / "index.json", {"files": {"json": "environment_variables.json"}}
    )
    _write_json(source / "testing" / "index.json", {"summary": {"total_tests": 1}})
    _write_json(source / "testing" / "test_inventory.json", {"tests_by_file": {}})
    _write_json(source / "testing" / "markers.json", {"markers": {}})
    _write_json(source / "validation" / "index.json", {"summary": {"total_validators": 1}})
    _write_json(source / "validation" / "validator_registry.json", {"validators": []})
    _write_json(source / "validation" / "rules_registry.json", {"rules": []})
    _write_json(source / "broker" / "index.json", {"protocols": ["BrokerProtocol"]})
    _write_json(source / "broker" / "api_reference.json", {"protocols": []})
    _write_json(source / "broker" / "examples.json", {"examples": []})
    _write_json(
        source / "reasoning" / "cli_flow_map.json",
        {"artifact": "cli_flow_map", "nodes": [{"id": "cli"}]},
    )
    _write_text(source / "reasoning" / "cli_flow_map.md")
    _write_json(
        source / "health" / "agent_health_schema.json",
        {"title": "Agent Health Report", "required": ["status"]},
    )
    _write_json(source / "health" / "agent_health_example.json", {"status": "passed"})
    return source


def test_validate_agent_artifacts_accepts_complete_tree(tmp_path: Path) -> None:
    source = _create_valid_agent_artifacts(tmp_path)

    report, summary = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert report.errors == []
    assert summary["file_count"] > 0
    assert set(agent_artifacts.EXPECTED_RESOURCES).issubset(summary["resources"])


def test_committed_agent_artifacts_index_matches_tree() -> None:
    report, _ = agent_artifacts.validate_agent_artifacts(
        agent_artifacts.DEFAULT_SOURCE_DIR,
        quiet=True,
    )

    assert report.errors == []


def test_validate_agent_artifacts_reports_missing_indexed_file(tmp_path: Path) -> None:
    source = _create_valid_agent_artifacts(tmp_path)
    (source / "schemas" / "risk_config_schema.json").unlink()

    report, _ = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert any("risk_config_schema.json" in error for error in report.errors)


def test_validate_agent_artifacts_allows_missing_optional_generated_file(
    tmp_path: Path,
) -> None:
    source = _create_valid_agent_artifacts(tmp_path)
    root_index_path = source / "index.json"
    root_index = json.loads(root_index_path.read_text(encoding="utf-8"))
    testing_resource = root_index["resources"]["testing"]
    testing_resource["files"].remove("test_inventory.json")
    testing_resource["optional_files"] = ["test_inventory.json"]
    _write_json(root_index_path, root_index)
    (source / "testing" / "test_inventory.json").unlink()

    report, summary = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert report.errors == []
    assert "testing/test_inventory.json" in summary["indexed_files"]


def test_validate_agent_artifacts_allows_optional_only_resource_with_absent_directory(
    tmp_path: Path,
) -> None:
    """A fully optional_files resource (e.g. testing) is valid on a fresh checkout."""
    source = _create_valid_agent_artifacts(tmp_path)
    root_index_path = source / "index.json"
    root_index = json.loads(root_index_path.read_text(encoding="utf-8"))
    testing_resource = root_index["resources"]["testing"]
    testing_resource["files"] = []
    testing_resource["optional_files"] = [
        "index.json",
        "markers.json",
        "test_inventory.json",
    ]
    _write_json(root_index_path, root_index)
    shutil.rmtree(source / "testing")

    report, summary = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert report.errors == []
    assert "testing/markers.json" in summary["indexed_files"]


def test_validate_agent_artifacts_validates_testing_index_when_present(
    tmp_path: Path,
) -> None:
    source = _create_valid_agent_artifacts(tmp_path)
    _write_json(source / "testing" / "index.json", {"summary": {}})

    report, _ = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert any("positive total_tests" in error for error in report.errors)


def test_validate_agent_artifacts_reports_resource_without_any_files(
    tmp_path: Path,
) -> None:
    source = _create_valid_agent_artifacts(tmp_path)
    root_index_path = source / "index.json"
    root_index = json.loads(root_index_path.read_text(encoding="utf-8"))
    root_index["resources"]["broker"]["files"] = []
    _write_json(root_index_path, root_index)

    report, _ = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert any("'broker' must list generated files" in error for error in report.errors)


def test_validate_agent_artifacts_allows_missing_optional_reasoning_machine_file(
    tmp_path: Path,
) -> None:
    """reasoning/*.json machine forms are optional; validate must pass without them."""
    source = _create_valid_agent_artifacts(tmp_path)
    root_index_path = source / "index.json"
    root_index = json.loads(root_index_path.read_text(encoding="utf-8"))
    reasoning_resource = root_index["resources"]["reasoning"]
    reasoning_resource["files"].remove("cli_flow_map.json")
    reasoning_resource["optional_files"] = ["cli_flow_map.json"]
    _write_json(root_index_path, root_index)
    (source / "reasoning" / "cli_flow_map.json").unlink()

    report, summary = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert report.errors == []
    assert "reasoning/cli_flow_map.json" in summary["indexed_files"]


def test_validate_agent_artifacts_reports_empty_optional_generated_file(
    tmp_path: Path,
) -> None:
    source = _create_valid_agent_artifacts(tmp_path)
    root_index_path = source / "index.json"
    root_index = json.loads(root_index_path.read_text(encoding="utf-8"))
    testing_resource = root_index["resources"]["testing"]
    testing_resource["files"].remove("test_inventory.json")
    testing_resource["optional_files"] = ["test_inventory.json"]
    _write_json(root_index_path, root_index)
    (source / "testing" / "test_inventory.json").write_text("", encoding="utf-8")

    report, _ = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert any("Optional artifact is empty" in error for error in report.errors)


def test_validate_agent_artifacts_ignores_gitignored_residue_but_reports_nonignored(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("naming_inventory.json\n", encoding="utf-8")
    source = _create_valid_agent_artifacts(tmp_path)
    _write_json(source / "naming_inventory.json", {"local_report": True})

    report, _ = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert report.errors == []

    _write_json(source / "unindexed_artifact.json", {"contract_drift": True})
    report, _ = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert any("unindexed_artifact.json" in error for error in report.errors)
    assert not any("naming_inventory.json" in error for error in report.errors)


def test_package_preserves_indexed_gitignored_artifact_without_residue(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text(
        "*.csv\nnaming_inventory.json\n",
        encoding="utf-8",
    )
    source = _create_valid_agent_artifacts(tmp_path)
    root_index_path = source / "index.json"
    root_index = json.loads(root_index_path.read_text(encoding="utf-8"))
    root_index["resources"]["testing"]["files"].append("data.csv")
    _write_json(root_index_path, root_index)
    _write_text(source / "testing" / "data.csv", "symbol,price\nBTC,1\n")
    _write_json(source / "naming_inventory.json", {"local_report": True})
    output_dir = tmp_path / "dist"

    report, summary = agent_artifacts.validate_agent_artifacts(source, quiet=True)
    package_status = agent_artifacts.package_agent_artifacts(
        source,
        output_dir,
        git_sha="abc123",
    )
    verify_status = agent_artifacts.verify_agent_artifact_package(
        output_dir / agent_artifacts.DEFAULT_PACKAGE_NAME,
        output_dir / agent_artifacts.DEFAULT_MANIFEST_NAME,
    )

    assert report.errors == []
    assert "testing/data.csv" in summary["indexed_files"]
    manifest = json.loads((output_dir / agent_artifacts.DEFAULT_MANIFEST_NAME).read_text())
    manifest_paths = {entry["path"] for entry in manifest["files"]}
    with tarfile.open(output_dir / agent_artifacts.DEFAULT_PACKAGE_NAME, "r:gz") as archive:
        tar_paths = {member.name for member in archive.getmembers() if member.isfile()}
    indexed_artifact = "var/agents/testing/data.csv"
    ignored_residue = "var/agents/naming_inventory.json"
    assert package_status == 0
    assert verify_status == 0
    assert indexed_artifact in manifest_paths
    assert indexed_artifact in tar_paths
    assert ignored_residue not in manifest_paths
    assert ignored_residue not in tar_paths


def test_validate_agent_artifacts_reports_empty_source(tmp_path: Path) -> None:
    source = tmp_path / "var" / "agents"
    source.mkdir(parents=True)

    report, _ = agent_artifacts.validate_agent_artifacts(source, quiet=True)

    assert report.errors == [f"Agent artifact directory is empty: {source}"]


def test_package_and_verify_agent_artifacts(tmp_path: Path) -> None:
    source = _create_valid_agent_artifacts(tmp_path)
    output_dir = tmp_path / "dist"

    package_status = agent_artifacts.package_agent_artifacts(
        source,
        output_dir,
        git_sha="abc123",
    )
    verify_status = agent_artifacts.verify_agent_artifact_package(
        output_dir / agent_artifacts.DEFAULT_PACKAGE_NAME,
        output_dir / agent_artifacts.DEFAULT_MANIFEST_NAME,
    )

    assert package_status == 0
    assert verify_status == 0
    manifest = json.loads((output_dir / agent_artifacts.DEFAULT_MANIFEST_NAME).read_text())
    assert manifest["git_sha"] == "abc123"
    assert manifest["file_count"] > 0


def test_verify_agent_artifact_package_rejects_unsafe_path(tmp_path: Path) -> None:
    output_dir = tmp_path / "dist"
    output_dir.mkdir()
    package_path = output_dir / agent_artifacts.DEFAULT_PACKAGE_NAME
    manifest_path = output_dir / agent_artifacts.DEFAULT_MANIFEST_NAME

    with tarfile.open(package_path, "w:gz") as archive:
        payload = tmp_path / "payload.txt"
        payload.write_text("bad\n", encoding="utf-8")
        archive.add(payload, arcname="../payload.txt")
    manifest = {
        "package_sha256": agent_artifacts._sha256_file(package_path),
        "package_prefix": agent_artifacts.DEFAULT_PACKAGE_PREFIX,
        "files": [{"path": "../payload.txt"}],
    }
    _write_json(manifest_path, manifest)

    status = agent_artifacts.verify_agent_artifact_package(package_path, manifest_path)

    assert status == 1
