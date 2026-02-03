# Use Python 3.11 as base image
FROM python:3.11-slim

# Set up timestamp helper script
COPY .devcontainer/timestamp_process_output.pl /timestamp_process_output.pl

# Create test artifacts directory
RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts

# Note: perl-base is already installed in python:3.11-slim
# No additional system dependencies needed for this project

# Copy uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set up working directory
WORKDIR /project_src

# Pre-create uv virtual environment and install dependencies
# Install dependencies first (without the project itself) to maximize layer caching
COPY pyproject.toml uv.lock /tmp/deps/
ENV UV_PROJECT_ENVIRONMENT=/venv
# Very important on Modal -- without this, there is a crazy bug that means that some files do not show up in snapshots!
# This is because Modal's snapshotting mechanism does not work correctly with symlinks.
ENV UV_LINK_MODE=copy
RUN cd /tmp/deps && \
    uv sync --no-install-project && \
    echo "Python virtual environment created successfully at $UV_PROJECT_ENVIRONMENT"

# Copy source code (these won't change during agent work)
COPY app.py ./
COPY tests/ ./tests/
COPY pyproject.toml uv.lock ./

# Add virtual environment to PATH
ENV PATH="/venv/bin:$PATH"

# Set PYTHONPATH to include project source
ENV PYTHONPATH="/project_src:${PYTHONPATH:-}"

# Copy the test runner script last (this changes often during development)
COPY .devcontainer/run_all_tests.sh /run_all_tests.sh
RUN chmod +x /run_all_tests.sh

# Default command
CMD ["/run_all_tests.sh"]
