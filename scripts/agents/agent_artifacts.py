#!/usr/bin/env python3
"""Validate, package, and verify generated agent artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "var" / "agents"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dist" / "agent-artifacts"
DEFAULT_PACKAGE_NAME = "agent-artifacts.tar.gz"
DEFAULT_MANIFEST_NAME = "agent-artifacts-manifest.json"
DEFAULT_PACKAGE_PREFIX = "var/agents"

EXPECTED_RESOURCES: tuple[str, ...] = (
    "schemas",
    "models",
    "logging",
    "observability",
    "configuration",
    "testing",
    "validation",
    "broker",
    "reasoning",
    "health",
)


@dataclass
class ValidationReport:
    """Collect validation diagnostics while keeping the CLI output readable."""

    source_dir: Path
    github_annotations: bool = False
    errors: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    def notice(self, message: str) -> None:
        self.notices.append(message)
        print(message)

    def error(self, message: str) -> None:
        self.errors.append(message)
        if self.github_annotations:
            print(f"::error::{message}", file=sys.stderr)
        else:
            print(f"ERROR: {message}", file=sys.stderr)


def _load_json(path: Path, report: ValidationReport) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        report.error(f"Missing JSON file: {path}")
    except json.JSONDecodeError as exc:
        report.error(f"Invalid JSON in {path}: {exc}")
    return None


def _json_mapping(path: Path, report: ValidationReport) -> dict[str, Any]:
    payload = _load_json(path, report)
    if isinstance(payload, dict):
        return payload
    if payload is not None:
        report.error(f"Expected JSON object in {path}")
    return {}


def _positive_number(value: Any) -> bool:
    return isinstance(value, int | float) and value > 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_root_for(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    root = result.stdout.strip()
    return Path(root) if root else None


def _gitignored_files(paths: list[Path], *, git_root: Path) -> set[Path]:
    resolved_git_root = git_root.resolve()
    relative_paths: list[str] = []
    path_by_relative: dict[str, Path] = {}
    for path in paths:
        try:
            relative = path.resolve().relative_to(resolved_git_root).as_posix()
        except ValueError:
            return set()
        relative_paths.append(relative)
        path_by_relative[relative] = path

    if not relative_paths:
        return set()

    ignored: set[Path] = set()
    for relative in relative_paths:
        for candidate in {relative, relative.replace("/", "\\")}:
            try:
                result = subprocess.run(
                    ["git", "-C", str(git_root), "check-ignore", "-q", "--", candidate],
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                return ignored
            if result.returncode == 0 and relative in path_by_relative:
                ignored.add(path_by_relative[relative])
                break

    return ignored


def _iter_source_files(
    source_dir: Path,
    *,
    indexed_files: set[str] | None = None,
) -> list[Path]:
    files = sorted(path for path in source_dir.rglob("*") if path.is_file())
    git_root = _git_root_for(source_dir)
    if git_root is None:
        return files

    ignored = _gitignored_files(files, git_root=git_root)
    return [
        path
        for path in files
        if path not in ignored
        or (indexed_files is not None and _relative_posix(path, source_dir) in indexed_files)
    ]


def _relative_posix(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _resource_file_names(
    resource_name: str,
    resource_payload: dict[str, Any],
    report: ValidationReport,
    *,
    key: str,
) -> list[str]:
    raw_files = resource_payload.get(key)
    if raw_files is None:
        return []
    if not isinstance(raw_files, list):
        report.error(f"Resource {resource_name!r} {key} must be a list")
        return []

    files: list[str] = []
    for file_name in raw_files:
        if not isinstance(file_name, str) or not file_name.strip():
            report.error(f"Resource {resource_name!r} contains an invalid {key} entry")
            continue
        files.append(file_name)
    return files


def _resource_files(resources: dict[str, Any], report: ValidationReport) -> set[str]:
    indexed_files: set[str] = {"index.json"}
    for resource_name, resource_payload in resources.items():
        if not isinstance(resource_payload, dict):
            report.error(f"Resource {resource_name!r} must be an object in var/agents/index.json")
            continue
        resource_path = resource_payload.get("path")
        if not isinstance(resource_path, str) or not resource_path.strip():
            report.error(f"Resource {resource_name!r} is missing a non-empty path")
            continue
        files = _resource_file_names(resource_name, resource_payload, report, key="files")
        optional_files = _resource_file_names(
            resource_name,
            resource_payload,
            report,
            key="optional_files",
        )
        if not files and not optional_files:
            report.error(f"Resource {resource_name!r} must list generated files")
        for file_name in [*files, *optional_files]:
            indexed_files.add(PurePosixPath(resource_path, file_name).as_posix())
    return indexed_files


def _indexed_files_from_root_index(root_index: dict[str, Any]) -> set[str]:
    indexed_files: set[str] = {"index.json"}
    resources = root_index.get("resources")
    if not isinstance(resources, dict):
        return indexed_files

    for resource_payload in resources.values():
        if not isinstance(resource_payload, dict):
            continue
        resource_path = resource_payload.get("path")
        if not isinstance(resource_path, str):
            continue
        for key in ("files", "optional_files"):
            files = resource_payload.get(key)
            if not isinstance(files, list):
                continue
            for file_name in files:
                if isinstance(file_name, str) and file_name.strip():
                    indexed_files.add(PurePosixPath(resource_path, file_name).as_posix())
    return indexed_files


def _validate_root_index(source_dir: Path, report: ValidationReport) -> dict[str, Any]:
    root_index = _json_mapping(source_dir / "index.json", report)
    resources = root_index.get("resources")
    if not isinstance(resources, dict):
        report.error("var/agents/index.json must contain a resources object")
        return {}

    missing_resources = sorted(set(EXPECTED_RESOURCES) - set(resources))
    if missing_resources:
        report.error(
            "var/agents/index.json is missing resource entries: " + ", ".join(missing_resources)
        )

    for resource_name, resource_payload in resources.items():
        if not isinstance(resource_payload, dict):
            report.error(f"Resource {resource_name!r} must be an object")
            continue

        resource_path = resource_payload.get("path")
        generator = resource_payload.get("generator")
        if not isinstance(resource_path, str) or not resource_path.strip():
            report.error(f"Resource {resource_name!r} is missing path")
            continue

        required_files = _resource_file_names(resource_name, resource_payload, report, key="files")
        optional_files = _resource_file_names(
            resource_name,
            resource_payload,
            report,
            key="optional_files",
        )
        if not required_files and not optional_files:
            report.error(f"Resource {resource_name!r} must list generated files")

        # A resource whose committed surface is entirely optional_files (e.g.
        # testing) legitimately has no directory on a fresh checkout; only
        # require the directory when committed files are expected in it.
        artifact_dir = source_dir / resource_path
        if required_files:
            if not artifact_dir.is_dir():
                report.error(f"Resource {resource_name!r} directory is missing: {artifact_dir}")
            elif not any(child.is_file() for child in artifact_dir.rglob("*")):
                report.error(f"Resource {resource_name!r} directory is empty: {artifact_dir}")

        for file_name in required_files:
            artifact_file = artifact_dir / file_name
            if not artifact_file.is_file():
                report.error(f"Indexed artifact is missing: {_display_path(artifact_file)}")
            elif artifact_file.stat().st_size == 0:
                report.error(f"Indexed artifact is empty: {_display_path(artifact_file)}")
        for file_name in optional_files:
            artifact_file = artifact_dir / file_name
            if artifact_file.is_file() and artifact_file.stat().st_size == 0:
                report.error(f"Optional artifact is empty: {_display_path(artifact_file)}")

        if isinstance(generator, str) and generator:
            generator_path = PROJECT_ROOT / generator
            if not generator_path.is_file():
                report.error(f"Generator for resource {resource_name!r} is missing: {generator}")

    indexed_files = _resource_files(resources, report)
    actual_files = {
        _relative_posix(path, source_dir)
        for path in _iter_source_files(source_dir, indexed_files=indexed_files)
        if path.name != ".gitkeep"
    }
    unindexed = sorted(
        path for path in actual_files - indexed_files if not path.endswith("/index.json")
    )
    if unindexed:
        report.error(
            "Generated files are not listed in var/agents/index.json: " + ", ".join(unindexed)
        )

    return root_index


def _validate_expected_content(source_dir: Path, report: ValidationReport) -> None:
    schemas_index = _json_mapping(source_dir / "schemas" / "index.json", report)
    if not isinstance(schemas_index.get("files"), dict) or not schemas_index["files"]:
        report.error("schemas/index.json must include a non-empty files mapping")

    models_index = _json_mapping(source_dir / "models" / "index.json", report)
    if not isinstance(models_index.get("files"), dict) or not models_index["files"]:
        report.error("models/index.json must include a non-empty files mapping")

    event_catalog = _json_mapping(source_dir / "logging" / "event_catalog.json", report)
    events = event_catalog.get("events")
    total_event_types = event_catalog.get("total_event_types")
    if not (
        (isinstance(events, list) and events)
        or (isinstance(events, dict) and events)
        or _positive_number(total_event_types)
    ):
        report.error("logging/event_catalog.json must describe at least one event type")

    metrics_catalog = _json_mapping(source_dir / "observability" / "metrics_catalog.json", report)
    if not isinstance(metrics_catalog.get("metrics"), list) or not metrics_catalog["metrics"]:
        report.error("observability/metrics_catalog.json must include at least one metric")

    environment_variables = _json_mapping(
        source_dir / "configuration" / "environment_variables.json",
        report,
    )
    if (
        not isinstance(environment_variables.get("variables"), list)
        or not environment_variables["variables"]
    ):
        report.error("configuration/environment_variables.json must include variables")

    # testing/* inventories are gitignored optional_files (regenerated on
    # demand); only validate the content when the file is present, e.g. right
    # after `uv run agent-regenerate` or inside the refresh/packaging workflow.
    testing_index_path = source_dir / "testing" / "index.json"
    if testing_index_path.is_file():
        testing_index = _json_mapping(testing_index_path, report)
        testing_summary = testing_index.get("summary")
        if not isinstance(testing_summary, dict) or not _positive_number(
            testing_summary.get("total_tests")
        ):
            report.error("testing/index.json must report a positive total_tests value")

    validation_index = _json_mapping(source_dir / "validation" / "index.json", report)
    validation_summary = validation_index.get("summary")
    if not isinstance(validation_summary, dict) or not _positive_number(
        validation_summary.get("total_validators")
    ):
        report.error("validation/index.json must report a positive total_validators value")

    broker_index = _json_mapping(source_dir / "broker" / "index.json", report)
    if not isinstance(broker_index.get("protocols"), list) or not broker_index["protocols"]:
        report.error("broker/index.json must include broker protocols")

    # reasoning/*.json machine forms are gitignored optional_files (regenerated
    # on demand); only validate the content when the file is present, e.g. right
    # after `uv run agent-regenerate` or inside the refresh/packaging workflow.
    cli_flow_path = source_dir / "reasoning" / "cli_flow_map.json"
    if cli_flow_path.is_file():
        cli_flow = _json_mapping(cli_flow_path, report)
        if cli_flow.get("artifact") != "cli_flow_map":
            report.error("reasoning/cli_flow_map.json must declare artifact=cli_flow_map")
        if not isinstance(cli_flow.get("nodes"), list) or not cli_flow["nodes"]:
            report.error("reasoning/cli_flow_map.json must include nodes")

    health_schema = _json_mapping(source_dir / "health" / "agent_health_schema.json", report)
    if health_schema.get("title") != "Agent Health Report":
        report.error("health/agent_health_schema.json must describe the Agent Health Report")
    if not isinstance(health_schema.get("required"), list) or not health_schema["required"]:
        report.error("health/agent_health_schema.json must include required fields")


def validate_agent_artifacts(
    source_dir: Path,
    *,
    github_annotations: bool = False,
    quiet: bool = False,
) -> tuple[ValidationReport, dict[str, Any]]:
    report = ValidationReport(source_dir=source_dir, github_annotations=github_annotations)

    if not source_dir.exists():
        report.error(f"Agent artifact directory is missing: {source_dir}")
        return report, {}
    if not source_dir.is_dir():
        report.error(f"Agent artifact path is not a directory: {source_dir}")
        return report, {}

    files = _iter_source_files(source_dir)
    if not files:
        report.error(f"Agent artifact directory is empty: {source_dir}")
        return report, {}

    root_index = _validate_root_index(source_dir, report)
    indexed_files = _indexed_files_from_root_index(root_index)
    _validate_expected_content(source_dir, report)
    files = _iter_source_files(source_dir, indexed_files=indexed_files)

    total_bytes = sum(path.stat().st_size for path in files)
    summary = {
        "source_dir": str(source_dir),
        "file_count": len(files),
        "indexed_files": sorted(indexed_files),
        "total_bytes": total_bytes,
        "resources": (
            sorted(root_index.get("resources", {}).keys())
            if isinstance(root_index.get("resources"), dict)
            else []
        ),
    }
    if not report.errors and not quiet:
        report.notice(
            "Validated agent artifacts: "
            f"{summary['file_count']} files, {summary['total_bytes']} bytes"
        )
    return report, summary


def _manifest_files(
    source_dir: Path,
    package_prefix: str,
    indexed_files: set[str],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in _iter_source_files(source_dir, indexed_files=indexed_files):
        relative = _relative_posix(path, source_dir)
        archive_path = PurePosixPath(package_prefix, relative).as_posix()
        entries.append(
            {
                "path": archive_path,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return entries


def _write_tar_gz(
    source_dir: Path,
    package_path: Path,
    package_prefix: str,
    indexed_files: set[str],
) -> None:
    package_path.parent.mkdir(parents=True, exist_ok=True)
    with package_path.open("wb") as raw_handle:
        with gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as gzip_handle:
            with tarfile.open(fileobj=gzip_handle, mode="w") as archive:
                for path in _iter_source_files(source_dir, indexed_files=indexed_files):
                    relative = _relative_posix(path, source_dir)
                    archive_name = PurePosixPath(package_prefix, relative).as_posix()
                    info = archive.gettarinfo(str(path), arcname=archive_name)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as file_handle:
                        archive.addfile(info, file_handle)


def package_agent_artifacts(
    source_dir: Path,
    output_dir: Path,
    *,
    package_prefix: str = DEFAULT_PACKAGE_PREFIX,
    git_sha: str | None = None,
    github_annotations: bool = False,
) -> int:
    report, summary = validate_agent_artifacts(
        source_dir,
        github_annotations=github_annotations,
        quiet=True,
    )
    if report.errors:
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    package_path = output_dir / DEFAULT_PACKAGE_NAME
    manifest_path = output_dir / DEFAULT_MANIFEST_NAME

    indexed_files = set(summary["indexed_files"])
    _write_tar_gz(source_dir, package_path, package_prefix, indexed_files)
    manifest_files = _manifest_files(source_dir, package_prefix, indexed_files)
    manifest = {
        "schema_version": "1.0",
        "created_at_unix": int(time.time()),
        "git_sha": git_sha or os.environ.get("GITHUB_SHA") or "",
        "source_dir": str(source_dir),
        "package": DEFAULT_PACKAGE_NAME,
        "package_sha256": _sha256_file(package_path),
        "package_prefix": package_prefix,
        "file_count": len(manifest_files),
        "total_bytes": summary["total_bytes"],
        "files": manifest_files,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"Packaged agent artifacts: {package_path}")
    print(f"Manifest: {manifest_path}")
    print(f"Package sha256: {manifest['package_sha256']}")
    return 0


def _safe_member_path(member_name: str) -> bool:
    path = PurePosixPath(member_name)
    return not path.is_absolute() and ".." not in path.parts


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    for member in archive.getmembers():
        if not _safe_member_path(member.name):
            raise ValueError(f"Unsafe archive path: {member.name}")
        archive.extract(member, destination, filter="data")


def verify_agent_artifact_package(
    package_path: Path,
    manifest_path: Path,
    *,
    github_annotations: bool = False,
) -> int:
    report = ValidationReport(source_dir=package_path.parent, github_annotations=github_annotations)

    manifest = _json_mapping(manifest_path, report)
    if not package_path.is_file():
        report.error(f"Package is missing: {package_path}")
        return 1
    if report.errors:
        return 1

    expected_digest = manifest.get("package_sha256")
    actual_digest = _sha256_file(package_path)
    if expected_digest != actual_digest:
        report.error(f"Package digest mismatch: expected {expected_digest}, got {actual_digest}")
        return 1

    expected_files = {
        entry.get("path")
        for entry in manifest.get("files", [])
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    if not expected_files:
        report.error("Manifest does not list packaged files")
        return 1

    with tarfile.open(package_path, mode="r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        member_names = {member.name for member in members}
        unsafe = sorted(member.name for member in members if not _safe_member_path(member.name))
        if unsafe:
            report.error("Package contains unsafe paths: " + ", ".join(unsafe))
            return 1

        missing = sorted(expected_files - member_names)
        if missing:
            report.error("Package is missing manifest files: " + ", ".join(missing))
            return 1

        with tempfile.TemporaryDirectory(prefix="agent-artifacts-package-") as temp_dir:
            extract_root = Path(temp_dir)
            _safe_extract(archive, extract_root)
            package_prefix = manifest.get("package_prefix") or DEFAULT_PACKAGE_PREFIX
            extracted_source = extract_root / package_prefix
            extracted_report, _ = validate_agent_artifacts(
                extracted_source,
                github_annotations=github_annotations,
                quiet=True,
            )
            if extracted_report.errors:
                return 1

    print(
        "Verified agent artifact package: " f"{len(expected_files)} files, sha256 {actual_digest}"
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate var/agents contents")
    validate_parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DIR)
    validate_parser.add_argument("--github-annotations", action="store_true")

    package_parser = subparsers.add_parser("package", help="Package var/agents for upload")
    package_parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DIR)
    package_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    package_parser.add_argument("--package-prefix", default=DEFAULT_PACKAGE_PREFIX)
    package_parser.add_argument("--git-sha", default=None)
    package_parser.add_argument("--github-annotations", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify-package",
        help="Verify a packaged var/agents upload",
    )
    verify_parser.add_argument(
        "--package",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / DEFAULT_PACKAGE_NAME,
        dest="package_path",
    )
    verify_parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / DEFAULT_MANIFEST_NAME,
    )
    verify_parser.add_argument("--github-annotations", action="store_true")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "validate":
        report, _ = validate_agent_artifacts(
            args.source,
            github_annotations=args.github_annotations,
        )
        return 1 if report.errors else 0
    if args.command == "package":
        return package_agent_artifacts(
            args.source,
            args.output_dir,
            package_prefix=args.package_prefix,
            git_sha=args.git_sha,
            github_annotations=args.github_annotations,
        )
    if args.command == "verify-package":
        return verify_agent_artifact_package(
            args.package_path,
            args.manifest,
            github_annotations=args.github_annotations,
        )

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
