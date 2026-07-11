from typing import Optional

from implicitdict import ImplicitDict, StringBasedDateTime

from monitoring.benchmarker.configurations.loads import OperationType
from monitoring.benchmarker.reports.report import (
    BenchmarkOperation,
    OperationsByOrigin,
    OperationsByOutcome,
    OperationsByType,
)
from monitoring.monitorlib.fetch import Query


class ExecutedOperation(ImplicitDict):
    """Record of an operation executed during a benchmark run, including type, origin, and outcome."""

    type: OperationType
    """The type of operation described by this record."""

    origin: str
    """Source/originator of this operation; e.g., the user that initiated this operation."""

    initiated_at: StringBasedDateTime
    """Time this operation was started/initiated."""

    completed_at: StringBasedDateTime
    """Time this operation completed, either successfully or in failure."""

    successful: bool
    """Whether this operation was successful (false for errors)."""

    query: Optional[Query]
    """The query details for this operation, if this was a query operation and query details are being recorded."""


def group_operations_by_type(
    scenario_ops: list[ExecutedOperation],
) -> list[OperationsByType]:
    """Convert/group a flat list of ExecutedOperation into hierarchical OperationsByType structure for reports or analysis."""
    ops_by_type_origin: dict[tuple[OperationType, str], list[ExecutedOperation]] = {}
    type_order: list[OperationType] = []
    origin_order_by_type: dict[OperationType, list[str]] = {}

    for op in scenario_ops:
        op_type: OperationType | None = getattr(op, "type", None) or op.get(
            "type", None
        )
        origin: str = getattr(op, "origin", None) or op.get("origin", "")
        if op_type is None:
            continue
        if op_type not in type_order:
            type_order.append(op_type)
            origin_order_by_type[op_type] = []
        if origin not in origin_order_by_type[op_type]:
            origin_order_by_type[op_type].append(origin)

        key = (op_type, origin)
        if key not in ops_by_type_origin:
            ops_by_type_origin[key] = []
        ops_by_type_origin[key].append(op)

    result: list[OperationsByType] = []
    for op_type in type_order:
        origins_list: list[OperationsByOrigin] = []
        for origin in origin_order_by_type[op_type]:
            ops = ops_by_type_origin[(op_type, origin)]
            successful_ops: list[BenchmarkOperation] = []
            unsuccessful_ops: list[BenchmarkOperation] = []
            for op in ops:
                clean_op = BenchmarkOperation(
                    t0=op.initiated_at,
                    t1=op.completed_at,
                )
                if "query" in op:
                    clean_op.query = op.query
                is_successful = getattr(op, "successful", False) or op.get(
                    "successful", False
                )
                if is_successful:
                    successful_ops.append(clean_op)
                else:
                    unsuccessful_ops.append(clean_op)

            outcome_record = OperationsByOutcome(
                successful=successful_ops if successful_ops else None,
                unsuccessful=unsuccessful_ops if unsuccessful_ops else None,
            )
            origins_list.append(
                OperationsByOrigin(
                    origin=origin,
                    outcomes=[outcome_record],
                )
            )
        result.append(
            OperationsByType(
                type=op_type,
                origins=origins_list,
            )
        )
    return result


def convert_to_operations_by_type(
    scenario_ops: list[ExecutedOperation],
) -> list[OperationsByType]:
    """Convert/group a flat list of ExecutedOperation into hierarchical OperationsByType structure for reports or analysis."""
    return group_operations_by_type(scenario_ops)
