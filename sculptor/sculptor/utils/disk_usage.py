from imbue_core.sculptor.telemetry import PosthogEventModel
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry import emit_posthog_event
from imbue_core.sculptor.telemetry_constants import ConsentLevel
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.sculptor.telemetry_utils import with_consent


class DiskUsageMeasurementPayload(PosthogEventPayload):
    saved_object_name: str = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="Name of the object saved to disk"
    )
    space_used_bytes: int = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="Space used by this item in bytes"
    )
    is_restart_required: bool = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS,
        description="Whether this object was large enough to trigger a container restart",
    )


def report_snapshot_to_posthog(snapshot_size_bytes: int, is_restart_required: bool) -> None:
    payload = DiskUsageMeasurementPayload(
        saved_object_name="Snapshot", space_used_bytes=snapshot_size_bytes, is_restart_required=is_restart_required
    )
    emit_posthog_event(
        PosthogEventModel(
            name=SculptorPosthogEvent.SNAPSHOT_SIZE_MEASUREMENT,
            component=ProductComponent.CROSS_COMPONENT,
            payload=payload,
        )
    )
