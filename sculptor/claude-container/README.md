# Claude Code container

* `Dockerfile.base_nix` is the Dockerfile used to build the base image ghcr.io/imbue-ai/sculptorbase_nix.

That image must be set to "public", otherwise you might see [errors](https://imbue-ai.slack.com/archives/C078ECJBU2G/p1751487036186749?thread_ts=1751487031.828429&cid=C078ECJBU2G) from CI/CD like:
```
providers/docker/image_builder.py:74:build_docker_image [tsk_01k31gqz]: - #3 ERROR:
failed to authorize: failed to fetch anonymous token: unexpected status from GET request to
https://ghcr.io/token?scope=repository%3Aimbue-ai%2Fsculptorbase_nix%3Apull&service=ghcr.io
```

This directory is its own UV workspace,
separate from the monorepo's main UV workspace.
When you run `uv` commands here (like `uv add`),
they will manipulate the dependencies of this UV workspace.

See also:
* [Imbue devcontainer spec](https://www.dropbox.com/scl/fi/u66adxkqfqifix9geyoaj/devcontainer.json-files-exist-lets-use-them.paper?rlkey=ws4g82lk01yfk221hxt7vzjh3&dl=0)
* [Devcontainer reference](https://containers.dev/implementors/json_reference/)
* [Custom Dockerfile spec](https://www.dropbox.com/scl/fi/nnym7ivlfbtmket6c544w/Custom-docker-files.paper?rlkey=h2l360w8h7ojtxf6zii3ou8ez&dl=0)

## Updating the base image

### When to update the base image

* If you have changed `Dockerfile.base_nix`,
  you should update the base image for it to take effect.

* If you want new imbue-verify wheels to be put into the imbue control plane.

### Prerequisites

1. Get someone to add you to our depot.dev account

2. Log in to depot.dev locally by calling `depot login`

3.  Authenticate to the github container registry (ghcr.io),
    following the [official documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-with-a-personal-access-token-classic). (You only need to do this once.)

    > **Important**: Your github account must be added to the imbue_ai organization to push images.

    ```shell
    echo $GITHUB_TOKEN_FOR_CONTAINER_PUSH | docker login ghcr.io -u USERNAME --password-stdin
    ```

### How to update the base image

1. Run this command:
    ```sh
    uv run sculptor/sculptor/cli/dev.py build-control-plane
    ```

    Note that it has some niceties like being able to debug (eg, run with a dirty state while modifying it) or run it without pushing (eg, for testing).
    Simply add a `--help` to see the details.

2. Commit the resulting changes (when the command finishes, it will write to files that reflect the git hash and image hash)

    > **Tip**: You can see the published images here:
    * https://github.com/orgs/imbue-ai/packages
    * https://github.com/orgs/imbue-ai/packages/container/package/sculptorbase_nix
    * https://depot.dev/orgs/qcdzrj4tbg/registry
    * https://github.com/imbue-ai/sculptorbase/pkgs/container/sculptorbase (Deprecated)
