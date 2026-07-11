import asyncio
import datetime
import random
import uuid
from collections.abc import Callable
from typing import Any

import s2sphere
from implicitdict import StringBasedDateTime

from monitoring.benchmarker.configurations.loads import OperationType, WorkflowType
from monitoring.benchmarker.configurations.users import (
    ASTMDSSSelectionStrategy,
    BenchmarkUserName,
    BenchmarkUserSpecification,
)
from monitoring.benchmarker.engine.operation import ExecutedOperation
from monitoring.monitorlib.fetch import Query
from monitoring.monitorlib.mutate.rid import ISAChange, delete_isa, put_isa
from monitoring.monitorlib.rid import RIDVersion
from monitoring.uss_qualifier.resources.astm.f3411.dss import (
    DSSInstance,
    DSSInstanceResource,
    DSSInstancesResource,
)
from monitoring.uss_qualifier.resources.definitions import ResourceID


class VirtualUser:
    """Base class for virtual users generating load."""

    def __init__(
        self,
        user_id: str,
        user_type_name: BenchmarkUserName,
        run_sync_client_call: Callable[..., Any],
        record_operation: Callable[[ExecutedOperation], None],
    ):
        self.user_id = user_id
        self.user_type_name = user_type_name
        self.run_sync_client_call = run_sync_client_call
        self.record_operation = record_operation

    async def run_workflow(self, stop_event: asyncio.Event) -> None:
        raise NotImplementedError()


class FlightPlannerUser(VirtualUser):
    """Virtual user implementing a flight planner behavior."""

    def __init__(
        self,
        user_id: str,
        user_spec: BenchmarkUserSpecification,
        resource_pool: dict[ResourceID, Any],
        run_sync_client_call: Callable[..., Any],
        record_operation: Callable[[ExecutedOperation], None],
    ):
        super().__init__(
            user_id, user_spec.name, run_sync_client_call, record_operation
        )
        self.flight_planner_spec = (
            user_spec.flight_planner
            if "flight_planner" in user_spec and user_spec.flight_planner is not None
            else None
        )
        if self.flight_planner_spec is None:
            raise NotImplementedError(
                f"User specification '{user_spec.name}' has no flight_planner definition"
            )

        # Resolve NetRID behavior and DSS instances
        self.netrid_behavior = (
            self.flight_planner_spec.astm_netrid_behavior
            if "astm_netrid_behavior" in self.flight_planner_spec
            and self.flight_planner_spec.astm_netrid_behavior is not None
            else None
        )
        if self.netrid_behavior is None:
            raise NotImplementedError(
                "Only astm_netrid_behavior is implemented for FlightPlannerUser"
            )

        if self.netrid_behavior.rid_version not in (
            RIDVersion.f3411_19,
            RIDVersion.f3411_22a,
        ):
            raise NotImplementedError(
                f"Unsupported RID version: {self.netrid_behavior.rid_version}"
            )

        self.dss_instances: list[DSSInstance] = []
        for res_id in self.netrid_behavior.dss_pool:
            if res_id not in resource_pool:
                raise ValueError(
                    f"Resource '{res_id}' in dss_pool not found in resource pool"
                )
            res = resource_pool[res_id]
            if isinstance(res, DSSInstancesResource):
                self.dss_instances.extend(res.dss_instances)
            elif isinstance(res, DSSInstanceResource):
                self.dss_instances.append(res.dss_instance)
            else:
                raise ValueError(
                    f"Resource '{res_id}' is not a DSSInstanceResource or DSSInstancesResource"
                )

        if not self.dss_instances:
            raise ValueError(
                f"No DSS instances resolved from dss_pool for user '{self.user_id}'"
            )

        self.dss_selection_strategy = (
            self.netrid_behavior.dss_selection_strategy
            if "dss_selection_strategy" in self.netrid_behavior
            and self.netrid_behavior.dss_selection_strategy is not None
            else ASTMDSSSelectionStrategy.First
        )
        if self.dss_selection_strategy not in (
            ASTMDSSSelectionStrategy.First,
            ASTMDSSSelectionStrategy.Random,
        ):
            raise NotImplementedError(
                f"DSS selection strategy '{self.dss_selection_strategy}' not implemented"
            )

        # Validate and resolve ISA strategy
        self.isa_strategy = (
            self.netrid_behavior.isa_strategy
            if "isa_strategy" in self.netrid_behavior
            and self.netrid_behavior.isa_strategy is not None
            else None
        )
        if (
            self.isa_strategy is None
            or "isa_per_flight" not in self.isa_strategy
            or self.isa_strategy.isa_per_flight is None
        ):
            raise NotImplementedError(
                "Only isa_per_flight strategy is implemented for FlightPlannerUser"
            )

        self.before_flight_start_s = self.isa_strategy.isa_per_flight.before_flight_start.timedelta.total_seconds()
        self.after_flight_end_s = (
            self.isa_strategy.isa_per_flight.after_flight_end.timedelta.total_seconds()
            if "after_flight_end" in self.isa_strategy.isa_per_flight
            and self.isa_strategy.isa_per_flight.after_flight_end is not None
            else 0.0
        )

        # Validate flight generation
        self.flight_gen = (
            self.flight_planner_spec.flight_generation.independent_time_location_shape
            if "independent_time_location_shape"
            in self.flight_planner_spec.flight_generation
            and self.flight_planner_spec.flight_generation.independent_time_location_shape
            is not None
            else None
        )
        if self.flight_gen is None:
            raise NotImplementedError(
                "Only independent_time_location_shape is implemented for flight_generation"
            )

        if (
            "fixed_spacing" not in self.flight_gen.time
            or self.flight_gen.time.fixed_spacing is None
        ):
            raise NotImplementedError(
                "Only fixed_spacing is implemented for flight_generation.time"
            )
        self.fixed_spacing_s = (
            self.flight_gen.time.fixed_spacing.timedelta.total_seconds()
        )

        if (
            "fixed_location" not in self.flight_gen.location
            or self.flight_gen.location.fixed_location is None
        ):
            raise NotImplementedError(
                "Only fixed_location is implemented for flight_generation.location"
            )
        self.fixed_loc = self.flight_gen.location.fixed_location

        if (
            "fixed_volumes" not in self.flight_gen.shape
            or self.flight_gen.shape.fixed_volumes is None
        ):
            raise NotImplementedError(
                "Only fixed_volumes is implemented for flight_generation.shape"
            )
        self.fixed_vols = self.flight_gen.shape.fixed_volumes

        # Check unit/reference match between fixed_location and fixed_volumes
        origin_vert = self.fixed_vols.origin_vertical
        if (
            self.fixed_loc.vertical.reference != origin_vert.reference
            or self.fixed_loc.vertical.units != origin_vert.units
        ):
            raise NotImplementedError(
                "Combining vertical location and shape with different reference or units is not supported"
            )
        self.alt_offset = self.fixed_loc.vertical.value - origin_vert.value
        self.lat_offset = (
            self.fixed_loc.horizontal.lat - self.fixed_vols.origin_horizontal.lat
        )
        self.lng_offset = (
            self.fixed_loc.horizontal.lng - self.fixed_vols.origin_horizontal.lng
        )

    def select_dss_instance(self) -> DSSInstance:
        if self.dss_selection_strategy == ASTMDSSSelectionStrategy.First:
            return self.dss_instances[0]
        else:
            return random.choice(self.dss_instances)

    def _record_query(self, query: Query, successful: bool | None = None) -> None:
        if query.query_type is None:
            raise NotImplementedError(
                f"Query type not specified for {query.request.method} query to {query.request.url}"
            )
        if successful is None:
            successful = query.status_code in (200, 201, 204)
        op = ExecutedOperation(
            type=OperationType(query.query_type),
            origin=self.user_id,
            initiated_at=StringBasedDateTime(query.request.timestamp),
            completed_at=StringBasedDateTime(query.response.reported.datetime),
            successful=successful,
            query=query,
        )
        self.record_operation(op)

    async def _sleep_interruptible(
        self, seconds: float, stop_event: asyncio.Event
    ) -> None:
        """Sleep in short intervals to wake up quickly if stop_event is set."""
        elapsed = 0.0
        while elapsed < seconds and not stop_event.is_set():
            slice_dur = min(0.1, seconds - elapsed)
            await asyncio.sleep(slice_dur)
            elapsed += slice_dur

    async def run_workflow(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            t_utm_start = datetime.datetime.now(datetime.UTC)
            flight_start_time = t_utm_start + datetime.timedelta(
                seconds=self.before_flight_start_s
            )
            time_offset = flight_start_time - self.fixed_vols.origin_time.datetime

            # Compute translated volumes and bounds for ISA
            min_alt = float("inf")
            max_alt = float("-inf")
            min_start = datetime.datetime.max.replace(tzinfo=datetime.UTC)
            max_end = datetime.datetime.min.replace(tzinfo=datetime.UTC)
            area_vertices: list[s2sphere.LatLng] = []

            for vol in self.fixed_vols.volumes:
                if (
                    "volume" not in vol
                    or vol.volume is None
                    or "altitude_lower" not in vol.volume
                    or vol.volume.altitude_lower is None
                    or "altitude_upper" not in vol.volume
                    or vol.volume.altitude_upper is None
                    or "time_start" not in vol
                    or vol.time_start is None
                    or "time_end" not in vol
                    or vol.time_end is None
                ):
                    raise NotImplementedError(
                        "Incomplete volume or altitude specifications in flight volume not supported"
                    )

                if (
                    vol.volume.altitude_lower.reference
                    != self.fixed_vols.origin_vertical.reference
                    or vol.volume.altitude_lower.units
                    != self.fixed_vols.origin_vertical.units
                    or vol.volume.altitude_upper.reference
                    != self.fixed_vols.origin_vertical.reference
                    or vol.volume.altitude_upper.units
                    != self.fixed_vols.origin_vertical.units
                ):
                    raise NotImplementedError(
                        "Flight volumes with differing vertical units/references not supported"
                    )

                lower_v = vol.volume.altitude_lower.value + self.alt_offset
                upper_v = vol.volume.altitude_upper.value + self.alt_offset
                start_v = vol.time_start.datetime + time_offset
                end_v = vol.time_end.datetime + time_offset

                min_alt = min(min_alt, lower_v)
                max_alt = max(max_alt, upper_v)
                min_start = min(min_start, start_v)
                max_end = max(max_end, end_v)

                if (
                    "outline_polygon" in vol.volume
                    and vol.volume.outline_polygon
                    and "vertices" in vol.volume.outline_polygon
                    and vol.volume.outline_polygon.vertices
                ):
                    for v in vol.volume.outline_polygon.vertices:
                        area_vertices.append(
                            s2sphere.LatLng.from_degrees(
                                v.lat + self.lat_offset, v.lng + self.lng_offset
                            )
                        )

            if not area_vertices:
                raise ValueError("No vertices found in flight shape to create ISA")

            dss_instance = self.select_dss_instance()
            isa_id = str(uuid.uuid4())
            uss_base_url = f"http://{self.user_id}.local"

            # 1. Create ISA in DSS
            isa_change: ISAChange = await self.run_sync_client_call(
                put_isa,
                area_vertices=area_vertices,
                alt_lo=min_alt,
                alt_hi=max_alt,
                start_time=min_start,
                end_time=max_end,
                uss_base_url=uss_base_url,
                isa_id=isa_id,
                rid_version=dss_instance.rid_version,
                utm_client=dss_instance.client,
                isa_version=None,
                participant_id=dss_instance.participant_id,
            )

            isa_success = isa_change.dss_query.success
            self._record_query(isa_change.dss_query.query, successful=isa_success)
            for notif in isa_change.notifications.values():
                self._record_query(notif.query)

            del_success = False

            if isa_success:
                # 2. Wait through flight duration until after_flight_end_s past the end of the flight
                now = datetime.datetime.now(datetime.UTC)
                target_del_time = max_end + datetime.timedelta(
                    seconds=self.after_flight_end_s
                )
                sleep_dur = (target_del_time - now).total_seconds()
                if sleep_dur > 0:
                    await self._sleep_interruptible(sleep_dur, stop_event)

                # 3. Delete ISA
                del_change: ISAChange = await self.run_sync_client_call(
                    delete_isa,
                    isa_id=isa_id,
                    isa_version=isa_change.dss_query.isa.version,
                    rid_version=dss_instance.rid_version,
                    utm_client=dss_instance.client,
                    participant_id=dss_instance.participant_id,
                )

                del_success = del_change.dss_query.success
                self._record_query(del_change.dss_query.query, successful=del_success)
                for notif in del_change.notifications.values():
                    self._record_query(notif.query)

            t_utm_end = datetime.datetime.now(datetime.UTC)
            flight_op = ExecutedOperation(
                type=OperationType(WorkflowType.FlightPlannerFlight),
                origin=self.user_id,
                initiated_at=StringBasedDateTime(t_utm_start),
                completed_at=StringBasedDateTime(t_utm_end),
                successful=isa_success and del_success,
                query=None,
            )
            self.record_operation(flight_op)

            if stop_event.is_set():
                break

            if self.fixed_spacing_s > 0:
                await self._sleep_interruptible(self.fixed_spacing_s, stop_event)


def create_virtual_user(
    user_id: str,
    user_spec: BenchmarkUserSpecification,
    resource_pool: dict[ResourceID, Any],
    run_sync_client_call: Callable[..., Any],
    record_operation: Callable[[ExecutedOperation], None],
) -> VirtualUser:
    if "flight_planner" in user_spec and user_spec.flight_planner is not None:
        return FlightPlannerUser(
            user_id, user_spec, resource_pool, run_sync_client_call, record_operation
        )
    else:
        raise NotImplementedError(
            f"User type '{user_spec.name}' has no implemented behavior"
        )
