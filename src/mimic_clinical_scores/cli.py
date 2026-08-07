"""Resumable command-line pipeline for cohort-filtered SAPS II."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from mimic_clinical_scores.common.cohort import (
    DEFAULT_SAMPLE_SEED,
    DEFAULT_SAMPLE_SIZE,
    inspect_cohort,
    prepare_all_icu_cohort,
    prepare_development_cohort,
)
from mimic_clinical_scores.common.concepts import build_concepts, require_tables
from mimic_clinical_scores.common.duckdb import (
    DuckDBSettings,
    connect,
    ensure_run_identity,
    remove_database_for_clean_rebuild,
)
from mimic_clinical_scores.common.export import export_all, validate_exports
from mimic_clinical_scores.common.preflight import identity_payload, run_preflight
from mimic_clinical_scores.common.provenance import atomic_write_json
from mimic_clinical_scores.common.staging import build_staging
from mimic_clinical_scores.scores.saps_ii.specification import SAPSII_SPEC


LOGGER = logging.getLogger("mimic_clinical_scores")


def _project_default() -> Path:
    return Path(os.environ.get("PROJECT_ROOT", Path.cwd()))


def _add_pipeline_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("dev100", "full"), required=True)
    parser.add_argument("--project-root", type=Path, default=_project_default())
    parser.add_argument("--mimic-root", type=Path, default=os.environ.get("MIMIC_ROOT"))
    parser.add_argument("--cohort-file", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--spill-directory", type=Path)
    parser.add_argument("--threads", type=int, default=int(os.environ.get("DUCKDB_THREADS", "4")))
    parser.add_argument("--memory-limit", default=os.environ.get("DUCKDB_MEMORY_LIMIT", "48GB"))
    parser.add_argument("--mimic-version", default="3.1")
    parser.add_argument("--verify-raw-checksums", action="store_true")
    parser.add_argument("--clean-rebuild", action="store_true")
    parser.add_argument(
        "--allow-clinical-scan",
        action="store_true",
        help="Required acknowledgement for commands that scan raw clinical event files",
    )
    parser.add_argument(
        "--confirm-full",
        action="store_true",
        help="Required in addition to --mode full for a raw-data staging/full run",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mimic-clinical-scores")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-cohort", help="Create the protected dev100 allowlist")
    prepare.add_argument("--project-root", type=Path, default=_project_default())
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--output", type=Path)
    prepare.add_argument("--manifest-output", type=Path)
    prepare.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    prepare.add_argument("--seed", type=int, default=DEFAULT_SAMPLE_SEED)
    prepare.add_argument("--overwrite", action="store_true")

    prepare_all = subparsers.add_parser(
        "prepare-all-icu-cohort",
        help="Create a protected allowlist from every raw MIMIC ICU stay",
    )
    prepare_all.add_argument("--project-root", type=Path, default=_project_default())
    prepare_all.add_argument("--mimic-root", type=Path, required=True)
    prepare_all.add_argument("--output", type=Path)
    prepare_all.add_argument("--manifest-output", type=Path)
    prepare_all.add_argument("--mimic-version", default="3.1")
    prepare_all.add_argument("--overwrite", action="store_true")
    prepare_all.add_argument("--allow-clinical-scan", action="store_true")

    for command, help_text in (
        ("preflight", "Validate metadata only; no complete clinical event scan"),
        ("build-staging", "Build normalized cohort-filtered raw tables"),
        ("build-concepts", "Build required upstream official concepts"),
        ("compute", "Execute the pinned official SAPS II SQL"),
        ("export", "Write atomic Parquet/CSV/JSON outputs"),
        ("validate", "Validate completed outputs"),
        ("run-all", "Run every resumable stage in order"),
    ):
        child = subparsers.add_parser(command, help=help_text)
        _add_pipeline_arguments(child)
    return parser


def _resolve_paths(args: argparse.Namespace) -> None:
    args.project_root = args.project_root.resolve()
    if args.mimic_root is None:
        raise ValueError("--mimic-root or MIMIC_ROOT is required")
    args.mimic_root = Path(args.mimic_root).resolve()
    if args.cohort_file is None:
        if args.mode == "full":
            raise ValueError("Full mode requires an explicit --cohort-file")
        args.cohort_file = args.project_root / "inputs" / "cohort_dev100.parquet"
    args.cohort_file = args.cohort_file.resolve()
    args.database = (args.database or args.project_root / "work" / args.mode / "saps_ii.duckdb").resolve()
    args.output_dir = (
        args.output_dir or args.project_root / "outputs" / args.mode / "saps_ii"
    ).resolve()
    args.log_dir = (args.log_dir or args.project_root / "logs" / args.mode).resolve()
    args.spill_directory = (
        args.spill_directory or args.project_root / "work" / args.mode / "spill"
    ).resolve()


def _configure_logging(log_dir: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_dir.chmod(0o700)
        log_path = log_dir / "pipeline.log"
        handler = logging.FileHandler(log_path, encoding="utf-8")
        os.chmod(log_path, 0o600)
        handlers.append(handler)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=handlers,
        force=True,
    )


def _load_cohort_manifest(args: argparse.Namespace) -> dict[str, Any] | None:
    candidate = args.cohort_file.with_name(f"{args.cohort_file.stem}_manifest.json")
    if not candidate.is_file():
        return None
    return json.loads(candidate.read_text(encoding="utf-8"))


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    report = run_preflight(
        project_root=args.project_root,
        mimic_root=args.mimic_root,
        cohort_file=args.cohort_file,
        mode=args.mode,
        verify_raw_checksums=args.verify_raw_checksums,
    )
    LOGGER.info(
        "preflight complete mode=%s cohort_rows=%d sources=%d concepts=%d",
        args.mode,
        report["cohort"]["rows"],
        len(report["raw_sources"]),
        len(report["official"]["dependency_order"]),
    )
    return report


def _clinical_scan_guard(args: argparse.Namespace) -> None:
    if not args.allow_clinical_scan:
        raise ValueError(
            "This command can scan raw clinical files. Re-run with --allow-clinical-scan "
            "only in the intended HPC job."
        )
    if args.mode == "full" and not args.confirm_full:
        raise ValueError("Full raw-data execution requires --confirm-full")


def _open_pipeline(args: argparse.Namespace, preflight: dict[str, Any]):
    settings = DuckDBSettings(
        database=args.database,
        threads=args.threads,
        memory_limit=args.memory_limit,
        spill_directory=args.spill_directory,
    )
    if args.clean_rebuild:
        remove_database_for_clean_rebuild(settings.database)
    connection = connect(settings)
    identity_hash = ensure_run_identity(
        connection,
        identity_payload(preflight, mimic_version=args.mimic_version),
    )
    return connection, settings, identity_hash


def _log_results(stage: str, results: list[dict[str, object]]) -> None:
    for result in results:
        LOGGER.info("stage=%s result=%s", stage, json.dumps(result, sort_keys=True, default=str))


def _run_pipeline_command(args: argparse.Namespace) -> dict[str, Any]:
    preflight = _preflight(args)
    if args.command == "preflight":
        report_path = args.log_dir / "preflight.json"
        atomic_write_json(report_path, preflight)
        return {"preflight": "passed", "report": str(report_path)}

    if args.command in {"build-staging", "run-all"}:
        _clinical_scan_guard(args)
    connection, settings, identity_hash = _open_pipeline(args, preflight)
    try:
        if args.command in {"build-staging", "run-all"}:
            cohort = inspect_cohort(args.cohort_file, mode=args.mode)
            staging_results = build_staging(
                connection,
                mimic_root=args.mimic_root,
                cohort=cohort,
                identity_hash=identity_hash,
                raw_metadata=preflight["raw_sources"],
                profile_directory=args.log_dir / "profiles",
            )
            _log_results("staging", staging_results)
            if args.command == "build-staging":
                return {"identity": identity_hash, "staging": staging_results}

        if args.command in {"build-concepts", "run-all"}:
            require_tables(
                connection,
                (
                    "mimiciv_hosp.admissions", "mimiciv_hosp.diagnoses_icd",
                    "mimiciv_hosp.labevents", "mimiciv_hosp.patients",
                    "mimiciv_hosp.services", "mimiciv_icu.chartevents",
                    "mimiciv_icu.icustays", "mimiciv_icu.outputevents",
                ),
            )
            concept_results = build_concepts(
                connection,
                concepts=SAPSII_SPEC.concepts,
                vendor_root=SAPSII_SPEC.vendor_root(args.project_root),
                identity_hash=identity_hash,
            )
            _log_results("concept", concept_results)
            if args.command == "build-concepts":
                return {"identity": identity_hash, "concepts": concept_results}

        if args.command in {"compute", "run-all"}:
            require_tables(connection, (concept.output_table for concept in SAPSII_SPEC.concepts))
            score_results = build_concepts(
                connection,
                concepts=(SAPSII_SPEC.score_concept,),
                vendor_root=SAPSII_SPEC.vendor_root(args.project_root),
                identity_hash=identity_hash,
            )
            _log_results("score", score_results)
            if args.command == "compute":
                return {"identity": identity_hash, "score": score_results}

        if args.command in {"export", "run-all"}:
            require_tables(connection, (SAPSII_SPEC.score_concept.output_table,))
            manifest = export_all(
                connection,
                output_directory=args.output_dir,
                identity_hash=identity_hash,
                mode=args.mode,
                mimic_version=args.mimic_version,
                cohort_manifest=_load_cohort_manifest(args),
                preflight=preflight,
                runtime=settings,
                command_line=sys.argv,
            )
            LOGGER.info("exports complete output=%s", args.output_dir)
            if args.command == "export":
                return {"identity": identity_hash, "outputs": manifest["output_paths"]}

        validation = validate_exports(
            connection, output_directory=args.output_dir, identity_hash=identity_hash
        )
        LOGGER.info("validation complete rows=%d", validation["cohort_rows"])
        return {"identity": identity_hash, "validation": validation}
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-cohort":
            _configure_logging()
            project_root = args.project_root.resolve()
            output = (args.output or project_root / "inputs" / "cohort_dev100.parquet").resolve()
            manifest = (
                args.manifest_output
                or project_root / "inputs" / "cohort_dev100_manifest.json"
            ).resolve()
            result = prepare_development_cohort(
                args.source.resolve(),
                output,
                manifest,
                sample_size=args.sample_size,
                seed=args.seed,
                overwrite=args.overwrite,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return
        if args.command == "prepare-all-icu-cohort":
            _configure_logging()
            if not args.allow_clinical_scan:
                raise ValueError(
                    "Preparing the all-ICU cohort scans raw icustays.csv.gz; "
                    "re-run with --allow-clinical-scan only in the intended HPC job"
                )
            project_root = args.project_root.resolve()
            output = (
                args.output or project_root / "inputs" / "cohort_all_icu.parquet"
            ).resolve()
            manifest = (
                args.manifest_output
                or project_root / "inputs" / "cohort_all_icu_manifest.json"
            ).resolve()
            result = prepare_all_icu_cohort(
                args.mimic_root.resolve(),
                output,
                manifest,
                mimic_version=args.mimic_version,
                overwrite=args.overwrite,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return
        _resolve_paths(args)
        _configure_logging(args.log_dir)
        result = _run_pipeline_command(args)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    except Exception as error:
        LOGGER.error("%s: %s", type(error).__name__, error)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
