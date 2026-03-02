"""Shared pytest configuration for evals."""

import logging

# Suppress noisy third-party loggers that spam DEBUG output,
# even when pytest is invoked with --log-cli-level=DEBUG.
_NOISY_LOGGERS = (
    "hpack",
    "httpcore",
    "httpx",
    "grpc",
    "h2",
    "websockets",
    "urllib3",
    "docker",
    "asyncio",
)
for _name in _NOISY_LOGGERS:
    logging.getLogger(_name).setLevel(logging.WARNING)
