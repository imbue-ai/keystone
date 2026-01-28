After creating a user's Docker image using the devcontainer spec, we inherit from their image with our own image.

Imbue's layers:
* Expect that `/imbue` will be volume mounted when the container is create to provide the Imbue control plane.
* Set up a symlink pointing from `/nix` to `/imbue/nix`.
* COPY the user's git repo into the correct place.
* Set some system configuration (tmux, sshd, users).
* Put our claude wrapper into `/imbue_addons/agent_path_extension_bin/claude`

Note that since we're volume-mounting the Imbue control plane in `/imbue/...` and `/imbue/nix/...`,
anything we eventually get from there is NOT yet available when we `docker build` the `Dockerfile.imbue_addons`.
That's the reason for the `imbue_post_container_build.sh` script in this directory, which
gets run after the container starts and `/imbue/...` and `/nix/...` are available.

## A note about system PATHs

Once wrapped by Imbue add-ons, the image supports two logically distinct system paths:

1. The user's environment, configured in the image that @Dockerfile.imbue_addons will inherit from.
2. The Imbue control plane, mostly mounted inside /imbue, with a bit of extra stuff in /imbue_addons.

To support this, we set two different $PATH variables:

1. _IMBUE_USER_ORIGINAL_PATH: The user's original $PATH, plus a few little Imbue bits.
2. _IMBUE_CONTROL_PLANE_PATH: The Imbue control plane $PATH.

Mixing these things was a source of surprising bugs and undeclared dependencies.
It leads to weird cross-talk, so we try to avoid it -- just pick one or the other.

The default $PATH for using this image is PATH=_IMBUE_CONTROL_PLANE_PATH.
Otherwise, commands run from python would be super annoying.
But when we run both `claude` and terminal, we restore the user's original PATH, so that they can use their tools.

Example bug: We used to have the user's PATH appended to the control plane PATH when running the control plane.
    Things mostly worked until we used a Docker image that did not include Git,
    at which point we realized that we were accidentally running the user's version of Git rather than the one from our control plane,
    but only in situations during which we arrived at the container via SSH.
By not including the user's PATH at the end of the control plane PATH, we make it harder to accidentally rely on the user's PATH.
So we try to keep them as separate as possible and be clear about which environment we are using at any given time.
Don't cross the streams!

# Expected layout

```
/imbue/: Imbue's control plane (Read Only, volume mounted)
  /imbue/nix/store: The entire Nix store that we depend on.
  /imbue/nix_bin: Symlinks to binaries in /nix/store, all in one place, ready for $PATH.  Get claude, git, bash, ncdu, less, strace, etc.
  /imbue/bin: Imbue's extra things we want on our $PATH.
  /imbue/imbue_env.sh: Environment variables required to make things work.
  /imbue/.venv: A Python environment where Imbue's CLIs are installed.
/nix: A symlink pointing at /imbue/nix
/imbue_addons/: A writeable layer created by Dockerfile.imbue_addons
  /imbue_addons/bin/claude: (Read-Write!) Our claude wrapper in a writeable place so that unit tests can overwrite it.
```

## Some useful commands for mucking around with devcontainers

```sh
# Once:
npm install -g @devcontainers/cli
# See: https://github.com/devcontainers/cli?tab=readme-ov-file#npm-install

export DEFAULT_DEVCONTAINER_IMAGE=$(
WF=sculptor/sculptor/services/environment_service/providers/docker/default_devcontainer && \
devcontainer build \
    --config $WF/devcontainer.json \
    --workspace-folder $WF \
|  jq -r '.imageName[0]'
)

IMBUE_ADDONS=sculptor/sculptor/services/environment_service/providers/docker/imbue_addons && \
export IMBUE_WRAPPED_IMAGE=$(
docker build --quiet \
    -f ${IMBUE_ADDONS}/Dockerfile.imbue_addons \
    ${IMBUE_ADDONS} \
    --build-arg BASE_IMAGE=${DEFAULT_DEVCONTAINER_IMAGE} \
    --build-context imbue_user_repo=$GI_ROOT
)

docker run \
    -u root \
    -v imbue_control_plane_20250916_ea70c3d9ff68558328e8be8d0aa43b67607aaf0d075e352e2291535a83ee230d:/imbue:ro \
    -it $IMBUE_WRAPPED_IMAGE bash
```
