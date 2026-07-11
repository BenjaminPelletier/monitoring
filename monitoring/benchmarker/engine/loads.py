import asyncio
import datetime
import time
from collections.abc import Callable
from typing import Any

from implicitdict import StringBasedDateTime
from loguru import logger

from monitoring.benchmarker.configurations.loads import (
    BenchmarkLoadSpecification,
    LoadCompletionCriteria,
    OperationType,
    StepCompletionCriteria,
    ThroughputStabilityCriteria,
    UserRampLoad,
)
from monitoring.benchmarker.configurations.users import BenchmarkUserSpecification
from monitoring.benchmarker.engine.operation import ExecutedOperation
from monitoring.benchmarker.engine.users import VirtualUser, create_virtual_user
from monitoring.benchmarker.reports.report import BenchmarkScenarioStepReport
from monitoring.uss_qualifier.resources.definitions import ResourceID


def _check_stability_criteria(
    criteria: ThroughputStabilityCriteria,
    operations: list[ExecutedOperation],
    step_ops_start_idx: int,
    virtual_users: list[VirtualUser],
) -> bool:
    if (
        "each_user_completed_at_least" in criteria
        and criteria.each_user_completed_at_least is not None
    ):
        req_count = criteria.each_user_completed_at_least.count
        req_ops = {str(o) for o in criteria.each_user_completed_at_least.operations}

        user_counts: dict[str, int] = {vu.user_id: 0 for vu in virtual_users}
        for op in operations[step_ops_start_idx:]:
            if op.successful and str(op.type) in req_ops and op.origin in user_counts:
                user_counts[op.origin] += 1

        return all(c >= req_count for c in user_counts.values())
    else:
        raise NotImplementedError(
            "Only each_user_completed_at_least is implemented in ThroughputStabilityCriteria"
        )


def _check_step_completion_criteria(
    criteria: StepCompletionCriteria,
    step_start_time: datetime.datetime,
    stability_time: datetime.datetime,
    now: datetime.datetime,
    operations: list[ExecutedOperation],
    stability_ops_idx: int,
) -> bool:
    has_any_of = "any_of" in criteria and criteria.any_of is not None
    has_duration = (
        "sampling_duration_at_least" in criteria
        and criteria.sampling_duration_at_least is not None
    )
    has_completed = (
        "completed_at_least" in criteria and criteria.completed_at_least is not None
    )
    has_average = (
        "average_duration_more_than" in criteria
        and criteria.average_duration_more_than is not None
    )
    has_stability_duration = (
        "throughput_stability_took_longer_than" in criteria
        and criteria.throughput_stability_took_longer_than is not None
    )

    if (
        not has_any_of
        and not has_duration
        and not has_completed
        and not has_average
        and not has_stability_duration
    ):
        raise NotImplementedError("StepCompletionCriteria has no specified conditions")

    if has_any_of and criteria.any_of is not None:
        if not any(
            _check_step_completion_criteria(
                child,
                step_start_time,
                stability_time,
                now,
                operations,
                stability_ops_idx,
            )
            for child in criteria.any_of
        ):
            return False

    if has_duration and criteria.sampling_duration_at_least is not None:
        req_dur = criteria.sampling_duration_at_least.timedelta.total_seconds()
        if (now - stability_time).total_seconds() < req_dur:
            return False

    if has_completed and criteria.completed_at_least is not None:
        req_count = criteria.completed_at_least.count
        req_ops = {str(o) for o in criteria.completed_at_least.operations}
        completed = sum(
            1
            for op in operations[stability_ops_idx:]
            if op.successful
            and str(op.type) in req_ops
            and op.completed_at.datetime <= now
        )
        if completed < req_count:
            return False

    if has_average and criteria.average_duration_more_than is not None:
        req_dur = criteria.average_duration_more_than.duration.timedelta.total_seconds()
        req_ops = {str(o) for o in criteria.average_duration_more_than.operations}
        matching_ops = [
            op
            for op in operations[stability_ops_idx:]
            if op.successful
            and str(op.type) in req_ops
            and stability_time <= op.completed_at.datetime <= now
        ]
        if not matching_ops:
            return False
        avg_dur = sum(
            (op.completed_at.datetime - op.initiated_at.datetime).total_seconds()
            for op in matching_ops
        ) / len(matching_ops)
        if avg_dur <= req_dur:
            return False

    if (
        has_stability_duration
        and criteria.throughput_stability_took_longer_than is not None
    ):
        req_dur = (
            criteria.throughput_stability_took_longer_than.timedelta.total_seconds()
        )
        if (stability_time - step_start_time).total_seconds() <= req_dur:
            return False

    return True


def _throughput_of_step_ops(
    steps: list[BenchmarkScenarioStepReport],
    operations: list[ExecutedOperation],
    step_index: int,
    op_types: set[str] | set[OperationType],
) -> float:
    step = steps[step_index]
    start_time = step.throughput_stability_time.datetime
    end_time = step.end_time.datetime
    count = 0
    for op in operations:
        if not op.successful or str(op.type) not in op_types:
            continue
        if op.completed_at.datetime < start_time or op.completed_at.datetime > end_time:
            continue
        count += 1
    dur = (end_time - start_time).total_seconds()
    return count / dur if dur > 0 else 0.0


def _check_load_completion_criteria(
    criteria: LoadCompletionCriteria,
    steps: list[BenchmarkScenarioStepReport],
    operations: list[ExecutedOperation],
) -> bool:
    has_any_of = "any_of" in criteria and criteria.any_of
    has_throughput = (
        "throughput_lower_than_peak" in criteria and criteria.throughput_lower_than_peak
    )
    has_failures = "failures_more_than" in criteria and criteria.failures_more_than
    has_most_recent_step = "most_recent_step" in criteria and criteria.most_recent_step

    if (
        not has_any_of
        and not has_throughput
        and not has_failures
        and not has_most_recent_step
    ):
        raise NotImplementedError("LoadCompletionCriteria has no specified conditions")

    if has_any_of and criteria.any_of is not None:
        if not any(
            _check_load_completion_criteria(child, steps, operations)
            for child in criteria.any_of
        ):
            return False

    if has_throughput and criteria.throughput_lower_than_peak is not None:
        if len(steps) < 2:
            return False
        op_types = {str(o) for o in criteria.throughput_lower_than_peak.operations}
        last_step_idx = len(steps) - 1
        last_tp = _throughput_of_step_ops(steps, operations, last_step_idx, op_types)
        peak_tp = max(
            _throughput_of_step_ops(steps, operations, idx, op_types)
            for idx in range(last_step_idx)
        )
        if (
            peak_tp <= 0
            or last_tp >= peak_tp * criteria.throughput_lower_than_peak.fraction_of_peak
        ):
            return False

    if has_failures and criteria.failures_more_than is not None:
        req_count = criteria.failures_more_than.count
        req_ops = {str(o) for o in criteria.failures_more_than.operations}
        fails = sum(
            1 for op in operations if not op.successful and str(op.type) in req_ops
        )
        if fails <= req_count:
            return False

    if has_most_recent_step and criteria.most_recent_step is not None:
        if not steps:
            return False
        last_step = steps[-1]
        step_start_time = last_step.start_time.datetime
        stability_time = last_step.throughput_stability_time.datetime
        now = last_step.end_time.datetime
        stability_ops_idx = 0
        for i, op in enumerate(operations):
            if op.completed_at.datetime >= stability_time:
                stability_ops_idx = i
                break
        else:
            stability_ops_idx = len(operations)
        if not _check_step_completion_criteria(
            criteria.most_recent_step,
            step_start_time,
            stability_time,
            now,
            operations,
            stability_ops_idx,
        ):
            return False

    return True


def _format_step_completion_progress(
    criteria: StepCompletionCriteria,
    step_start_time: datetime.datetime,
    stability_time: datetime.datetime,
    now: datetime.datetime,
    operations: list[ExecutedOperation],
    stability_ops_idx: int,
) -> list[str]:
    parts: list[str] = []
    if (
        "sampling_duration_at_least" in criteria
        and criteria.sampling_duration_at_least is not None
    ):
        req_dur = criteria.sampling_duration_at_least.timedelta.total_seconds()
        cur_dur = (now - stability_time).total_seconds()
        parts.append(
            f"sampling_duration_at_least (threshold: {req_dur:.1f}s, current: {cur_dur:.1f}s)"
        )
    if "completed_at_least" in criteria and criteria.completed_at_least is not None:
        req_count = criteria.completed_at_least.count
        req_ops = {str(o) for o in criteria.completed_at_least.operations}
        completed = sum(
            1
            for op in operations[stability_ops_idx:]
            if op.successful
            and "type" in op
            and str(op.type) in req_ops
            and op.completed_at.datetime <= now
        )
        ops_str = ", ".join(sorted(req_ops))
        parts.append(
            f"completed_at_least (threshold: {req_count} of [{ops_str}], current: {completed})"
        )
    if (
        "average_duration_more_than" in criteria
        and criteria.average_duration_more_than is not None
    ):
        req_dur = criteria.average_duration_more_than.duration.timedelta.total_seconds()
        req_ops = {str(o) for o in criteria.average_duration_more_than.operations}
        matching_ops = [
            op
            for op in operations[stability_ops_idx:]
            if op.successful
            and "type" in op
            and str(op.type) in req_ops
            and stability_time <= op.completed_at.datetime <= now
        ]
        if matching_ops:
            cur_dur = sum(
                (op.completed_at.datetime - op.initiated_at.datetime).total_seconds()
                for op in matching_ops
            ) / len(matching_ops)
            cur_str = f"{cur_dur:.1f}s"
        else:
            cur_str = "N/A"
        ops_str = ", ".join(sorted(req_ops))
        parts.append(
            f"average_duration_more_than (threshold: {req_dur:.1f}s of [{ops_str}], current: {cur_str})"
        )
    if (
        "throughput_stability_took_longer_than" in criteria
        and criteria.throughput_stability_took_longer_than is not None
    ):
        req_dur = (
            criteria.throughput_stability_took_longer_than.timedelta.total_seconds()
        )
        cur_dur = (stability_time - step_start_time).total_seconds()
        parts.append(
            f"throughput_stability_took_longer_than (threshold: {req_dur:.1f}s, current: {cur_dur:.1f}s)"
        )
    if "any_of" in criteria and criteria.any_of is not None:
        child_parts = []
        for child in criteria.any_of:
            sub = _format_step_completion_progress(
                child,
                step_start_time,
                stability_time,
                now,
                operations,
                stability_ops_idx,
            )
            if sub:
                child_parts.append("(" + " AND ".join(sub) + ")")
        if child_parts:
            parts.append(" OR ".join(child_parts))
    return parts


def _get_operations_of_interest(
    criteria: StepCompletionCriteria,
    all_step_ops: list[ExecutedOperation],
    with_defaults: bool = False,
) -> set[str]:
    ops: set[str] = set()
    if "completed_at_least" in criteria and criteria.completed_at_least is not None:
        for o in criteria.completed_at_least.operations:
            ops.add(str(o))
    if (
        "average_duration_more_than" in criteria
        and criteria.average_duration_more_than is not None
    ):
        for o in criteria.average_duration_more_than.operations:
            ops.add(str(o))
    if "any_of" in criteria and criteria.any_of is not None:
        for child in criteria.any_of:
            ops.update(_get_operations_of_interest(child, all_step_ops))
    if not ops and with_defaults:
        import json

        logger.error(f"No operations of interest for {json.dumps(criteria)}")
        ops = {str(op.type) for op in all_step_ops if "type" in op}
    return ops


def _format_waiting_status(
    ramp: UserRampLoad,
    step_index: int,
    operations: list[ExecutedOperation],
    step_ops_start_idx: int,
    stability_ops_idx: int,
    step_start_time: datetime.datetime | None,
    stability_time: datetime.datetime | None,
    virtual_users: list[VirtualUser],
) -> str:
    if stability_time is None:
        if (
            "each_user_completed_at_least" in ramp.throughput_stability_criteria
            and ramp.throughput_stability_criteria.each_user_completed_at_least
            is not None
        ):
            crit = ramp.throughput_stability_criteria.each_user_completed_at_least
            req_count = crit.count
            req_ops = {str(o) for o in crit.operations}
            user_counts: dict[str, int] = {vu.user_id: 0 for vu in virtual_users}
            for op in operations[step_ops_start_idx:]:
                if (
                    op.successful
                    and "type" in op
                    and str(op.type) in req_ops
                    and op.origin in user_counts
                ):
                    user_counts[op.origin] += 1
            counts = list(user_counts.values())
            met_users = sum(1 for c in counts if c >= req_count)
            max_c = max(counts) if counts else 0
            min_c = min(counts) if counts else 0
            ops_str = ", ".join(sorted(req_ops))
            return f"[Step {step_index} Waiting for throughput stability] each_user_completed_at_least (threshold: {req_count} of [{ops_str}]): {met_users}/{len(virtual_users)} users met threshold | most advanced user: {max_c} completed, least advanced user: {min_c} completed"
        else:
            return f"[Step {step_index} Waiting for throughput stability] (evaluating stability criteria)"
    else:
        now = datetime.datetime.now(datetime.UTC)
        progress = (
            _format_step_completion_progress(
                ramp.step_completion_criteria,
                step_start_time,
                stability_time,
                now,
                operations,
                stability_ops_idx,
            )
            if step_start_time is not None
            else []
        )
        cond_str = (
            " AND ".join(progress)
            if progress
            else "(evaluating step completion criteria)"
        )
        return f"[Step {step_index} Waiting for step completion] {cond_str}"


async def run_scenario_load(
    load_spec: BenchmarkLoadSpecification,
    user_specs_map: dict[str, BenchmarkUserSpecification],
    resource_pool: dict[ResourceID, Any],
    run_sync_client_call: Callable[..., Any],
    record_operation: Callable[[ExecutedOperation], None],
    operations: list[ExecutedOperation],
    steps: list[BenchmarkScenarioStepReport],
) -> None:
    """Execute a scenario load by driving virtual user workflows and monitoring step criteria."""
    if "user_ramp" not in load_spec or load_spec.user_ramp is None:
        raise NotImplementedError(
            f"Load specification '{load_spec.name}' has no user_ramp defined"
        )
    ramp = load_spec.user_ramp

    ramp_user_type = str(ramp.user_type)
    if ramp_user_type not in user_specs_map:
        raise ValueError(
            f"User type '{ramp_user_type}' not found in configuration.user_types"
        )
    user_spec = user_specs_map[ramp_user_type]

    active_tasks: list[asyncio.Task] = []
    virtual_users: list[VirtualUser] = []
    stop_event = asyncio.Event()

    current_load_factor = ramp.initial_users
    step_index = 0
    step_start_time: datetime.datetime | None = None
    step_ops_start_idx = len(operations)
    stability_time: datetime.datetime | None = None
    stability_ops_idx = len(operations)

    last_status_time = [time.monotonic()]

    def update_status_time() -> None:
        last_status_time[0] = time.monotonic()

    def wrapped_record_op(op: ExecutedOperation) -> None:
        update_status_time()
        record_operation(op)

    logger.info(
        f"Starting user_ramp load '{load_spec.name}' with initial_users={current_load_factor}"
    )
    update_status_time()

    async def _periodic_summary_logger() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(1.0)
            now_t = time.monotonic()
            if now_t - last_status_time[0] >= 30.0 and not stop_event.is_set():
                last_status_time[0] = now_t
                msg = _format_waiting_status(
                    ramp,
                    step_index,
                    operations,
                    step_ops_start_idx,
                    stability_ops_idx,
                    step_start_time,
                    stability_time,
                    virtual_users,
                )
                logger.info(msg)

    summary_task = asyncio.create_task(_periodic_summary_logger())

    try:
        while not stop_event.is_set():
            step_start_time = datetime.datetime.now(datetime.UTC)
            step_ops_start_idx = len(operations)

            first_user_spawned = None
            last_user_spawned = None
            while len(virtual_users) < current_load_factor:
                user_id = f"{user_spec.name}_{len(virtual_users) + 1}"
                if first_user_spawned is None:
                    first_user_spawned = user_id
                last_user_spawned = user_id
                vu = create_virtual_user(
                    user_id,
                    user_spec,
                    resource_pool,
                    run_sync_client_call,
                    wrapped_record_op,
                )
                virtual_users.append(vu)
                active_tasks.append(asyncio.create_task(vu.run_workflow(stop_event)))
            if first_user_spawned and last_user_spawned:
                logger.info(
                    f"Spawned virtual users '{first_user_spawned}' to '{last_user_spawned}'"
                )
                update_status_time()

            # Monitor for ThroughputStabilityCriteria
            stability_time = None
            while stability_time is None and not stop_event.is_set():
                if _check_stability_criteria(
                    ramp.throughput_stability_criteria,
                    operations,
                    step_ops_start_idx,
                    virtual_users,
                ):
                    stability_time = datetime.datetime.now(datetime.UTC)
                    logger.info(
                        f"Step {step_index} reached throughput stability after {(stability_time - step_start_time).total_seconds():.1f}s"
                    )
                    update_status_time()
                    break
                await asyncio.sleep(0.5)

            if stop_event.is_set() or stability_time is None:
                break

            stability_ops_idx = len(operations)
            step_end_time = datetime.datetime.now(datetime.UTC)

            # Monitor for StepCompletionCriteria
            while not stop_event.is_set():
                now = datetime.datetime.now(datetime.UTC)
                if _check_step_completion_criteria(
                    ramp.step_completion_criteria,
                    step_start_time,
                    stability_time,
                    now,
                    operations,
                    stability_ops_idx,
                ):
                    step_end_time = now
                    break
                await asyncio.sleep(0.5)

            step_ops = operations[step_ops_start_idx:]
            ops_of_interest = _get_operations_of_interest(
                ramp.step_completion_criteria, step_ops, True
            )
            dur_valid = (
                (step_end_time - stability_time).total_seconds()
                if stability_time
                else 0.0
            )
            dur_step = (
                (step_end_time - step_start_time).total_seconds()
                if step_start_time
                else dur_valid
            )

            valid_ops_matching = [
                op
                for op in operations[stability_ops_idx:]
                if "type" in op
                and str(op.type) in ops_of_interest
                and op.successful
                and op.completed_at.datetime <= step_end_time
            ]
            valid_count = len(valid_ops_matching)
            tp_valid = valid_count / dur_valid if dur_valid > 0 else 0.0

            step_ops_matching = [
                op
                for op in step_ops
                if "type" in op
                and str(op.type) in ops_of_interest
                and op.successful
                and op.completed_at.datetime <= step_end_time
            ]
            step_count = len(step_ops_matching)

            fails_by_type: dict[str, int] = {}
            for op in step_ops:
                if (
                    not op.successful
                    and op.completed_at.datetime <= step_end_time
                    and "type" in op
                ):
                    t_str = str(op.type)
                    fails_by_type[t_str] = fails_by_type.get(t_str, 0) + 1

            failures_str = (
                ", ".join(f"{k}: {v}" for k, v in sorted(fails_by_type.items()))
                if fails_by_type
                else "0 failures"
            )
            ops_interest_str = (
                ", ".join(sorted(ops_of_interest))
                if ops_of_interest
                else "all operations"
            )

            logger.info(
                f"Step {step_index} completed (load_factor={current_load_factor}, operations of interest: [{ops_interest_str}]):\n"
                f"  • Validity Period Throughput: {tp_valid:.2f} ops/s across validity duration ({dur_valid:.1f}s)\n"
                f"  • Operations of Interest Completed: {valid_count} in validity period ({dur_valid:.1f}s), {step_count} in full step duration ({dur_step:.1f}s)\n"
                f"  • Failures during step: {failures_str}"
            )
            update_status_time()

            if stop_event.is_set():
                break

            step_report = BenchmarkScenarioStepReport(
                load_factor=float(current_load_factor),
                start_time=StringBasedDateTime(step_start_time),
                throughput_stability_time=StringBasedDateTime(stability_time),
                end_time=StringBasedDateTime(step_end_time),
            )
            steps.append(step_report)

            # Check LoadCompletionCriteria
            if _check_load_completion_criteria(
                ramp.load_completion_criteria, steps, operations
            ):
                logger.info(
                    f"Load completion criteria met after step {step_index}. Stopping load."
                )
                update_status_time()
                stop_event.set()
                break

            step_index += 1
            current_load_factor += ramp.additional_users_per_step
            logger.info(
                f"Advancing to step {step_index} with load_factor={current_load_factor}"
            )
            update_status_time()
    finally:
        summary_task.cancel()
        if not stop_event.is_set():
            stop_event.set()
        logger.info(
            f"Waiting for {len(active_tasks)} active virtual users to wind down gracefully..."
        )
        await asyncio.gather(*active_tasks, return_exceptions=True)
        logger.info("All virtual users have finished.")
