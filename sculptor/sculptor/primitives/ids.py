import hashlib
from abc import ABC
from typing import Generic

from typeid.constants import SUFFIX_LEN as TYPEID_SUFFIX_LEN

from imbue_core.agents.data_types.ids import ObjectID
from imbue_core.ids import ExternalID
from sculptor.services.environment_service.providers.provider_types import DockerMarker
from sculptor.services.environment_service.providers.provider_types import LocalMarker
from sculptor.services.environment_service.providers.provider_types import ModalMarker
from sculptor.services.environment_service.providers.provider_types import ProviderMarkerT


class RequestID(ObjectID):
    tag: str = "rqst"


class UserSettingsID(ObjectID):
    tag: str = "usr"


class ImageID(ObjectID):
    tag: str = "img"


class LocalImageID(ImageID):
    tag: str = "loc_img"


class TransactionID(ObjectID):
    tag: str = "txn"


class ObjectSnapshotID(ObjectID):
    tag: str = "snap"


class ModalImageObjectID(ExternalID):
    pass


class DockerImageID(ExternalID):
    pass


class EnvironmentID(ExternalID, Generic[ProviderMarkerT], ABC):
    pass


class ModalSandboxObjectID(EnvironmentID[ModalMarker]):
    pass


class LocalEnvironmentID(EnvironmentID[LocalMarker]):
    pass


class DockerContainerID(EnvironmentID[DockerMarker]):
    pass


EnvironmentIDTypes = DockerContainerID | LocalEnvironmentID | ModalSandboxObjectID


class UserReference(ExternalID):
    """
    Reference to a user record in the identity provider's system. (Authentik at the moment.)

    """


class OrganizationReference(ExternalID):
    """
    Reference to an organization record in the identity provider's system. (Authentik at the moment.)

    """


def get_deterministic_typeid_suffix(seed: str) -> str:
    raw_digest = hashlib.md5(seed.encode()).hexdigest()
    return "0" + raw_digest[: TYPEID_SUFFIX_LEN - 1].lower()


def _create_hash_from_string_seed(key: str) -> str:
    return hashlib.md5(key.encode()).hexdigest()


def create_user_id(email: str) -> str:
    return _create_hash_from_string_seed(email)


def create_organization_id(email: str) -> str:
    return _create_hash_from_string_seed(f"organization:{email}")
