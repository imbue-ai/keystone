from typing import Annotated
from typing import TypeVar

from pydantic import Tag

from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.pydantic_serialization import build_discriminator


class ModalMarker(SerializableModel):
    pass


class DockerMarker(SerializableModel):
    pass


class LocalMarker(SerializableModel):
    pass


ProviderMarkerTypes = Annotated[
    Annotated[ModalMarker, Tag("ModalMarker")]
    | Annotated[DockerMarker, Tag("DockerMarker")]
    | Annotated[LocalMarker, Tag("LocalMarker")],
    build_discriminator(),
]

ProviderMarkerT = TypeVar("ProviderMarkerT", bound=ProviderMarkerTypes)
