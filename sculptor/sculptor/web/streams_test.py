import pytest

from imbue_core.agents.data_types.ids import ProjectID
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import Notification
from sculptor.database.models import NotificationID
from sculptor.database.models import Project
from sculptor.database.models import UserSettings
from sculptor.primitives.ids import OrganizationReference
from sculptor.primitives.ids import UserReference
from sculptor.primitives.ids import UserSettingsID
from sculptor.services.data_model_service.api import CompletedTransaction
from sculptor.web.data_types import UserUpdateSourceTypes
from sculptor.web.streams import _convert_to_user_update


def test_convert_to_user_update_collects_models_and_overwrites_duplicates() -> None:
    organization = OrganizationReference("org-ref")
    user_reference = UserReference("user-ref")
    project_id = ProjectID()

    initial_project = Project(object_id=project_id, organization_reference=organization, name="Initial")
    updated_project = initial_project.model_copy(update={"name": "Updated"})

    user_settings = UserSettings(
        object_id=UserSettingsID(),
        user_reference=user_reference,
        is_usage_data_enabled=True,
    )

    server_settings = SculptorSettings(DEV_MODE=True, DOMAIN="example.test")

    notification = Notification(
        object_id=NotificationID(),
        user_reference=user_reference,
        message="Streaming notification",
    )

    transactions: list[UserUpdateSourceTypes | None] = [
        None,
        CompletedTransaction(request_id=None, updated_models=(initial_project,)),
        server_settings,
        CompletedTransaction(request_id=None, updated_models=(updated_project, user_settings, notification)),
    ]

    update = _convert_to_user_update(transactions)

    assert update.user_settings == user_settings
    assert update.projects == (updated_project,)
    assert update.settings == server_settings
    assert update.notifications == (notification,)


def test_convert_to_user_update_raises_for_unexpected_models() -> None:
    with pytest.raises(AssertionError):
        # pyre-ignore[6]: the test is checking that there's an assertion error if we input an invalid type
        _convert_to_user_update(["unexpected model"])
