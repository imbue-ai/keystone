# Quickstart

## Prerequisites

* Clone this repo
  * You have to generate an access token and use that as your password when cloning: <https://gitlab.com/-/user_settings/personal_access_tokens>
* Install Homebrew
  * Find install script at <https://brew.sh/>
  * Note the commands that it tells you to run after the script completes
* Install tmux: `brew install tmux`
* Install just: `brew install just`
* Install uv: `brew install uv`
* Install mutagen: `brew install mutagen-io/mutagen/mutagen`
* Install nvm: <https://github.com/nvm-sh/nvm?tab=readme-ov-file#install--update-script>
* Install pre-commit hook: `uv run pre-commit install`
* Install tailscale: $repo/docs/tailscale.md
* Install vault: $repo/vault/docs/getting_started.md
* Extract API keys: `$repo/scripts/export-vault-env shared/ANTHROPIC_API_KEY shared/OPENAI_API_KEY`
* Install watchman for pyre: `brew install watchman`

Follow the sculptor external setup guide: <https://imbue-ai.notion.site/A-Guide-to-Sculptor-22aa550faf95801b8639dd3288e21974?source=copy_link>

## Running sculptor locally

From the root of the generally intelligent repo, run the following command to build the project:

```bash
cd sculptor
just install build || { just clean install build ; }
```

Then run the following command to start the frontend and backend in a tmux session (this will also install dependencies):

```bash
just start

# Alternatively to run not in a tmux session, you can run the following commands:
# Run the following in separate terminals
# backend
just backend
# frontend
just frontend
```

Note, you may need to clear your state if we've made any updates. Especially after pulling new changes from main, run:

```bash
just clean install generate-api
```

or

```bash
mv ~/.sculptor ~/.sculptor.bkp
mv ~/.dev_sculptor ~/.dev_sculptor.bkp
```

If you are testing changes that affect the docker container (for example, if you made changes to imbue-cli), there is a convenience `just` target to incorporate those changes:

```bash
export SCULPTOR_CONTROL_PLANE_VOLUME=<your_control_plane_volume_name>
just build-local-control-plane

# Not when you run just start, `<your_control_plane_volume_name>` will be used as the volume name for the control plane.
```

See the justfile for all supported commands.

## Changing the database

By default, Sculptor saves its data in a semi-ephemeral way in an SQLite database.

If you'd like to change this, set the DATABASE_URL environment variable. For example:

* `DATABASE_URL="sqlite:////var/lib/sculptor/sculptor.db" uv run fastapi run sculptor/server.py`
* `DATABASE_URL="postgresql+psycopg://..." uv run fastapi run sculptor/server.py`

## Tests

```bash
just test-unit
just test-integration
```

## Authentication

By default, authentication is off. If you want to enable it, set the `ALLOW_ANONYMOUS_USERS` environment variable to `false`.

When you do that, you need to authenticate using the `Authorization: Bearer` header, e.g.:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:5050/api/v1/auth/me
```

You can get a token by actually running sculptor in your web browser. The frontend will notice that it received 401 or 403 responses from the backend and will redirect you to our [Authentik server](https://auth.imbue.com/). After completing the whole login flow, you will find your JWT in localStorage under the `sculptor-jwt` key.

For more details, see the docstring in the [auth.py](sculptor/web/auth.py) module.

## Learning More

Take a look at the [docs/](docs/README.md) folder to learn more about the architecture, design, and implementation details of Sculptor.
