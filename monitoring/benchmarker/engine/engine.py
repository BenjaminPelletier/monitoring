import asyncio
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from monitoring.benchmarker.artifacts.generation import generate_artifacts
from monitoring.benchmarker.configurations.configuration import BenchmarkConfiguration
from monitoring.benchmarker.configurations.users import BenchmarkUserSpecification
from monitoring.benchmarker.engine.actions import get_repo_root, run_scenario_actions
from monitoring.benchmarker.engine.loads import run_scenario_load
from monitoring.benchmarker.engine.operation import (
    ExecutedOperation,
    convert_to_operations_by_type,
)
from monitoring.benchmarker.engine.resources import instantiate_resources
from monitoring.benchmarker.reports.report import (
    BenchmarkReport,
    BenchmarkRunReport,
    BenchmarkScenarioReport,
    BenchmarkScenarioStepReport,
)


def get_version_info() -> tuple[str, str]:
    """Retrieve codebase version and git commit hash."""
    commit_hash = "unknown"
    codebase_version = "unknown"

    repo_root = get_repo_root()
    try:
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    try:
        from monitoring._version import __version__ as ver

        codebase_version = ver
    except ImportError:
        pass

    return codebase_version, commit_hash


async def _run_benchmark_async(
    config: BenchmarkConfiguration,
    output_dir: str,
    codebase_version: str,
    commit_hash: str,
) -> BenchmarkRunReport:
    logger.info("Instantiating declared resources...")
    resource_pool = instantiate_resources(config)

    user_specs_map: dict[str, BenchmarkUserSpecification] = {
        str(u.name): u for u in config.user_types
    }
    loads_map = {str(load.name): load for load in config.loads}

    max_io_threads = 400
    executor = ThreadPoolExecutor(
        max_workers=max_io_threads, thread_name_prefix="benchmarker_io_"
    )

    async def run_sync_client_call(
        func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))

    scenarios_reports: list[BenchmarkScenarioReport] = []

    try:
        for scenario_spec in config.scenarios:
            logger.info(
                f"========== Starting Scenario '{scenario_spec.name}' =========="
            )
            scenario_ops: list[ExecutedOperation] = []
            scenario_steps: list[BenchmarkScenarioStepReport] = []

            def record_operation(op: ExecutedOperation) -> None:
                scenario_ops.append(op)
                if not op.successful:
                    if "query" in op and op.query is not None:
                        details = (
                            op.query.failure_details
                            or op.query.error_message
                            or op.query.response.failure
                            or f"HTTP {op.query.status_code}"
                        )
                        logger.warning(
                            f"Operation '{op.type}' from origin '{op.origin}' failed on {op.query.request.method} {op.query.request.url} ({op.query.status_code}): {details}"
                        )
                    else:
                        logger.warning(
                            f"Operation '{op.type}' from origin '{op.origin}' failed."
                        )

            all_actions = config.actions if "actions" in config else None

            # 1. Run setup actions
            setup_actions = scenario_spec.setup if "setup" in scenario_spec else None
            run_scenario_actions(setup_actions, all_actions)

            # 2. Run load
            if str(scenario_spec.load) not in loads_map:
                raise ValueError(
                    f"Scenario load '{scenario_spec.load}' not defined in configuration.loads"
                )
            load_spec = loads_map[str(scenario_spec.load)]
            await run_scenario_load(
                load_spec,
                user_specs_map,
                resource_pool,
                run_sync_client_call,
                record_operation,
                scenario_ops,
                scenario_steps,
            )

            # 3. Run teardown actions
            teardown_actions = (
                scenario_spec.teardown if "teardown" in scenario_spec else None
            )
            run_scenario_actions(teardown_actions, all_actions)

            scenario_report = BenchmarkScenarioReport(
                operations=convert_to_operations_by_type(scenario_ops),
                steps=scenario_steps,
                metadata=(
                    dict(scenario_spec.metadata)
                    if "metadata" in scenario_spec and scenario_spec.metadata
                    else {}
                ),
            )
            scenarios_reports.append(scenario_report)
            logger.info(
                f"========== Completed Scenario '{scenario_spec.name}' =========="
            )
    finally:
        executor.shutdown(wait=True)

    run_report = BenchmarkRunReport(
        codebase_version=codebase_version,
        commit_hash=commit_hash,
        configuration=config,
        report=BenchmarkReport(scenarios=scenarios_reports),
    )

    if "artifacts" in config and config.artifacts:
        logger.info("Generating configured artifacts...")
        generate_artifacts(config.artifacts, run_report, output_dir)
        logger.info("Artifact generation complete.")
    else:
        logger.warning("No artifacts specified in configuration.")

    return run_report


def run_benchmark(
    config: BenchmarkConfiguration, output_dir: str
) -> BenchmarkRunReport:
    """Execute the benchmarker engine for the provided configuration and save artifacts to output_dir."""
    codebase_version, commit_hash = get_version_info()
    return asyncio.run(
        _run_benchmark_async(config, output_dir, codebase_version, commit_hash)
    )
