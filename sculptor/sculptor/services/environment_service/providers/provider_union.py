from sculptor.services.environment_service.providers.docker.docker_provider import DockerProvider
from sculptor.services.environment_service.providers.local.local_provider import LocalProvider
from sculptor.services.environment_service.providers.modal.modal_provider import ModalProvider

ProviderUnion = ModalProvider | DockerProvider | LocalProvider
