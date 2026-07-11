from typing import Any

from monitoring.benchmarker.configurations.configuration import BenchmarkConfiguration
from monitoring.uss_qualifier.resources.definitions import ResourceID
from monitoring.uss_qualifier.resources.resource import create_resources


def instantiate_resources(config: BenchmarkConfiguration) -> dict[ResourceID, Any]:
    """Instantiate all resources declared in the benchmark configuration."""
    if (
        "resources" not in config
        or not config.resources
        or "resource_declarations" not in config.resources
        or not config.resources.resource_declarations
    ):
        return {}
    return create_resources(
        config.resources.resource_declarations,
        resource_source="benchmark configuration",
        stop_when_not_created=True,
    )
