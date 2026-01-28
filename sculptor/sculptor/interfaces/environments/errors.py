from imbue_core.errors import ExpectedError


class EnvironmentFailure(ExpectedError):
    """Errors related to environments."""


class EnvironmentNotFoundError(EnvironmentFailure):
    """Could not find (or start) an old environment."""


class EnvironmentConfigurationChangedError(EnvironmentFailure):
    """When the configuration has changed, we can no longer start the previous Environment."""


class FileNotFoundEnvironmentError(EnvironmentFailure, FileNotFoundError):
    """Error raised when a file is not found."""


class FileOrDirectoryCouldNotBeDeletedError(EnvironmentFailure, OSError):
    """Error raised when a file or directory could not be deleted."""


class IsADirectoryEnvironmentError(EnvironmentFailure, IsADirectoryError):
    """Error raised when a path is a directory."""


class ProviderError(EnvironmentFailure):
    """Error raised when a provider is misconfigured, unavailable, etc."""


class ImageConfigError(EnvironmentFailure):
    """Error raised when an image config or Dockerfile is invalid."""


class ImageNotFoundError(EnvironmentFailure):
    """Error raised when an image is missing."""


class EnvironmentAlreadyExistsError(EnvironmentFailure):
    """Error raised trying to create an Environment, but one with the same name already exists."""


class SetupError(EnvironmentFailure):
    """Error raised when an environment setup fails."""


class ProviderNotFoundError(EnvironmentFailure):
    """Error raised when a provider cannot be found."""


class EnvironmentProviderCleanupError(EnvironmentFailure):
    pass


class EnvironmentNotHealthy(ExpectedError):
    pass
