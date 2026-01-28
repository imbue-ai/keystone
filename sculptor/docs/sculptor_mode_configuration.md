# Sculptor Mode Configuration

Sculptor now supports different operational modes to enable split deployment scenarios where the frontend (Electron app) and backend can run in different locations.

## Environment Variable

Use the `SCULPTOR_MODE` environment variable to configure how Sculptor starts up.

## Available Modes

### 1. Default Mode, SCULPTOR_MODE=''
- **Behavior**: Standard Sculptor operation - both frontend and backend run together
- **Backend**: Started automatically by the Electron app (unless in development mode without `START_BACKEND_IN_DEV`)

### 2. `client_only`
- **Behavior**: Frontend-only mode - Electron app runs without starting the backend
  ```bash
  # Start backend separately on a remote server (remember to set SCULPTOR_FRONTEND_PORT and/or SCULPTOR_FRONTEND_HOST there to make CORS work!)
  # Forward the port back to localhost

  # Then start frontend in client_only mode
  SCULPTOR_MODE=client_only SCULPTOR_SESSION_TOKEN=<> SCULPTOR_API_PORT=5050 ....
  ```

### 3. `headless`
- **TODO**. Current version minimizes the window. Maybe:
  - Remove dependency on xvfb
  - Option to run without creating a window at all
  - Create a minimal blank page instead of full UI


# start the backend headless. xvfb needed to run if your server doesnt have a xwindow up. see https://imbue-ai.slack.com/archives/C05RX4T4UHJ/p1763523015098969?thread_ts=1763506533.350659&cid=C05RX4T4UHJ

`SCULPTOR_MODE=headless xvfb-run -a ./Sculptor.AppImage --appimage-extract-and-run . --no-sandbox`
```
