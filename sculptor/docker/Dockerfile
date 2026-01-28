# syntax = docker/dockerfile:1.2.0

FROM ubuntu:20.04 AS requirements_assembler

RUN mkdir -p /opt/projects/generally_intelligent

RUN --mount=type=bind,source=./,target=/tmp/ \
  bash /tmp/docker/copy_package_files.sh /tmp /opt/projects/generally_intelligent

FROM ubuntu:20.04

WORKDIR /tmp

CMD ["/main.sh"]
EXPOSE 65022

COPY ./docker/00_* /tmp/
RUN bash 00_apt_install.sh

COPY ./docker/01_* /tmp/
RUN bash 01_user_config.sh

COPY ./docker/20_* /tmp/
RUN bash 20_install_ttyd.sh

COPY ./docker/90_* /tmp/
RUN bash 90_authorized_keys.sh

COPY ./docker/99_container_main.sh /main.sh
RUN chmod ugo+x /main.sh

COPY ./docker/10_* /tmp/
RUN bash 10_heart_beat.sh

COPY --from=ghcr.io/imbue-ai/uv_with_debug_python:d84ff4b1f4337e0607a57c09486e9cd68d3932bb /uv /uvx /bin/
USER user
ENV UV_LINK_MODE=copy

WORKDIR "/opt/projects/generally_intelligent/sculptor"

COPY ./crafty/docker/35_* /tmp/
RUN sudo bash /tmp/35_firefox_install.sh

COPY ./sculptor/docker/67_* /tmp/
RUN bash /tmp/67_install_claude.sh

RUN sudo apt install -y docker-ce

RUN git config --global user.email "buildbot@imbue.com" && \
    git config --global user.name "Buildbot"

# app_basic_test.py needs a non-shallow repo
RUN mkdir -p /tmp/repo && \
    cd /tmp/repo && \
    git init . && \
    git checkout -b main && \
    touch f && \
    git add f && \
    git commit -m 'NOTE(Dockerfile): init commit'

RUN curl -L -o /tmp/mutagen_linux_amd64_v0.18.1.tar.gz https://github.com/mutagen-io/mutagen/releases/download/v0.18.1/mutagen_linux_amd64_v0.18.1.tar.gz && \
    cd /usr/local/bin/ && \
    sudo tar xvf /tmp/mutagen_linux_amd64_v0.18.1.tar.gz && \
    rm /tmp/mutagen_linux_amd64_v0.18.1.tar.gz

# NOTE(bowei): idk where this installs playwright to, but we lift this here primarily to get the apt deps
RUN --mount=type=cache,target=/home/user/.cache/uv,sharing=private,uid=1000,gid=1000\
    uvx playwright install --with-deps && sudo uvx playwright install --with-deps

# Xvfb is needed for running tests against Electron
RUN sudo apt install -y xvfb

#################################################################################
# copies in the pyproject.toml from the repo -- cant do any uv stuff before this
#################################################################################
COPY --from=requirements_assembler --chown=user:user /opt/projects/generally_intelligent /opt/projects/generally_intelligent

RUN --mount=type=cache,target=/home/user/.cache/uv,sharing=private,uid=1000,gid=1000\
    uv venv --python 3.11 /opt/venv

ENV VIRTUAL_ENV=/opt/venv

# NOTE(bowei): I dont know why this is needed but otherwise computronium fails to load.
RUN --mount=type=cache,target=/home/user/.cache/uv,sharing=private,uid=1000,gid=1000\
  cd /opt/projects/generally_intelligent/sculptor && uv sync && uv sync --active && \
    cd /opt/projects/generally_intelligent && uv sync && uv sync --active
RUN \
  cd /opt/projects/generally_intelligent/sculptor && uv sync && uv sync --active && \
    cd /opt/projects/generally_intelligent && uv sync && uv sync --active

RUN --mount=type=cache,target=/home/user/.cache/uv,sharing=private,uid=1000,gid=1000\
    cd /opt/projects/generally_intelligent/sculptor && uv sync --dev --active --locked --no-install-workspace --all-extras

# installs jupyter through uv
COPY ./docker/60_* /tmp/
RUN --mount=type=cache,target=/home/user/.cache/uv,sharing=private,uid=1000,gid=1000\
    cd /opt/projects/generally_intelligent/standalone/research && bash /tmp/60_install_jupyter.sh

# Note the sudo -- needed because sometimes we need playwright as root, and sometimes not
RUN --mount=type=cache,target=/home/user/.cache/uv,sharing=private,uid=1000,gid=1000\
    unset UV_INDEX_URL && uv run --project sculptor -m playwright install --with-deps && \
    sudo uv run --project sculptor -m playwright install --with-deps

# Installs just
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    . $HOME/.cargo/env && \
    cargo install just


#################################################################################
# copies in the npm bits
#################################################################################
COPY --chown=user:user ./sculptor/frontend/package*.json /opt/projects/generally_intelligent/sculptor/frontend/
COPY --chown=user:user ./sculptor/frontend/.npmrc /opt/projects/generally_intelligent/sculptor/frontend/

RUN cd frontend && npm install

#################################################################################
# copies in the full repo -- make sure this is the last step
#################################################################################
COPY --chown=user:user . /opt/projects/generally_intelligent

# rerun the other steps, for good measure
RUN uv sync --dev --active --locked --all-extras && uv sync --dev --locked --all-extras

RUN cd frontend && npm install

# NOTE(bowei): I dont know why this is needed but otherwise computronium fails to load. and must be this order. Tests it too.
RUN cd /opt/projects/generally_intelligent && uv sync --active && cd /home/user && uv run python3 -m computronium.remote_wrapper --help

# RUN make install-test || sudo make install-test # This doesn't actually save any time

WORKDIR /home/user
