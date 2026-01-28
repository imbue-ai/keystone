# Config Service Revamp


## Motivation

We already have a config service but it's relatively ad-hoc, with not much of an internal structure. It combines various things together in a flat structure and the responsibilities between the service and its caller (e.g. `agents/default/claude_code_sdk`) overlap in unclear ways.


## Requirements

- Functional:
  - Manage the configuration of a potentially wide portfolio of third party tools and services (claude code, codex, gitlab, github, modal, ...).
  - Support non-sensitive configuration as well as secrets.
  - Allow callers to retrieve / set / modify relevants config parts.
  - Run maintenance background jobs (e.g. OAuth token refresh routines).
  - Register and notify watchers (e.g. to immediately react to claude code configuration changes).
  - If needed, manage sculptor's internal configuration, too.

- Non-functional:
  - Do not mix logic belonging to different third-party services together.
  - Keep all pieces of logic related to a particular third-party service co-located in the codebase.
  - Offer a clear boundary between the config service's responsibilities and the responsibilities of its caller.


## Proposed architecture

Let's organize the functionality into plugins. Each service (claude, codex, gitlab, ...) will have their own config service plugin, typically located outside of the config service itself, alongside the rest of the code that interfaces with the third-party service. That way the plugin can naturally reuse constants, conventions and utilities with the rest of the interfacing module, without leaking them to the rest of the codebase.

A plugin has a prescribed lifecycle (`start()`, `stop()`, it gets its own concurrency group and a reference to a shared `Observer` instance). Other than that, for flexibility, it does not need to implement a specific interface for the actual configuration management. Each plugin can offer different functionality.

For this to work well with type checkers, the config service will explicitly list its plugins as top-level class attributes. This will require some additional care to avoid circular imports but we think it's worth the flexibility in terms of plugin interfaces / functionality.

For a module whose purpose is to interface with a third-party tool or service, functionality that manages global and persistent state should typically be built in the form of a config service plugin. The rest of the functionality should ideally be stateless.

Sculptor-focused configuration management that is not related to any third-party tool or service (e.g. user secrets) should be implemented directly on the ConfigService itself. Plugins get a reference to a ConfigService instance and can use it, too.

For consistency with the "api" vs "implementation" convention we adopted for services (including the ConfigService), we will do the same for config service plugins. By placing these two in different modules, we can avoid circular imports.


## Other thoughts

### Task environment synchronization

One of the prominent use cases is the synchronization of configuration changes into task environments. We considered several different approaches: lazy just-in-time configuration collection, continuous watcher-based synchronization or synchronization based on explicit user action (button click).

Where possible, we prefer continuous watcher-based synchronization to update all active task containers in real time:

- Conceptually, the mental model for users seems to be the simplest ("my local settings are synchronized at all times").
- We're able to immediately react to configuration changes with UI notifications.
- Task environments are immediately updated even when no messages are sent (useful e.g. when interacting with the agent's terminal).
- Continuous real-time updates are consistent with our product philosophy (see pairing mode).
- Behavior of in-sculptor agents is consistent with the behavior of local agents.

### Cloud-hosted sculptor

If we ever deploy sculptor instances in the cloud, we have at least two basic options of how to deal with the different requirements for the ConfigService in a hosted environment:

1. Provide different plugin implementations for different environments (cloud vs local).
2. Fake the desktop environment on the server. (E.g. by creating `~/.claude/*`.)
