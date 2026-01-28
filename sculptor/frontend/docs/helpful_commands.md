## Helpful flags and commands

Cleans previously installed dependencies and builds everything required for a production build.
```bash
just refresh
```

Packages the electron app and creates a Sculptor.app file in `<repo_root>/dist`.
```bash
just app
```

Notarizing and signing can be really slow, this skips it for local testing.
```bash
SKIP_NOTARIZE_AND_SIGN=1 just app
```

Starts the electron app in development mode. This accepts an already running backend.
```bash
just start
```

Starts the electron app in development mode. This will start a backend for us.
```bash
# TODO: we should be able to just run the development version of the backend here
just refresh
START_BACKEND_IN_DEV=1 just start
```
