# Sculptor Posthog Integration

Sculptor uses [Posthog](https://posthog.com/) to perform product analytics and error tracking with strict content-filtering based on user consent levels.

- List of posthog events: https://us.posthog.com/project/136453/data-management/events
- List of posthog actions: https://us.posthog.com/project/136453/data-management/actions
- imbue.com (dev) instance: https://us.posthog.com/project/198112/data-management/events

## Core Dashboards

1. ["Sculptor Product Dashboard"](https://us.posthog.com/project/136453/dashboard/459087) be our north-star metrics
2. ["FUN Sculptor Dashboard"](https://us.posthog.com/project/136453/dashboard/509340) for all your awesome ad-hoc product insights!! (<-- pls try out new charts in here)
3. [WIP] "Sculptor Productionization Dashboard" for adding stuff relating to devOps-ish stuff

## Key Posthog Terminology

### 1. Person

Posthog automatically creates a new user to associate events with. Each person is identified by a list of IDs (more on this later - [alias](https://posthog.com/docs/product-analytics/identify#alias-assigning-multiple-distinct-ids-to-the-same-user)), and is keyed by some UUID generated within the project scope.

### 2. Events

Posthog essentially dumps everything into the `events` table and each new captured log arrives as a new 'event' with it's identifying distinct_id as well as `event` (event_name).

> **Note:** Contents of each event row are stored as Json blobs, so it is very easy to club our schema if we're not careful here. All events logged by Sculptor adheres to the `PosthogEventModel` schema. Add entries into `telemetry_constants.py` and an associated `PosthogEventPayload` for additional content storage Pydantic models to ensure we're consistent for downstream consumption.

### 3. Actions

Actions group semantically similar events into a higher level of abstraction. For example, what does it mean for a user to be 'active'? Is it viewing Sculptor home_page? Starting a new task? Submitting a message on an ongoing task? Maybe it's all of the above!

Actions (e.g. [User Message Sent](https://us.posthog.com/project/136453/data-management/actions/183943)) allow us to define conditions upon multiple raw events.

This also provides a more stable point of layering Insights upon, even if we later migrate or add new events that contribute to certain metrics.

> **Tip:** always try to add an 'Action' for something you want to measure and track as an Insight.

### 4. Insights

Insights are these nice charts we see on dashboards! For example here are a few of our key metrics:
1. [User message count](https://us.posthog.com/project/136453/insights/FkSJefVM?dashboard=459087&variables_override=%7B%7D)
2. [Onboarding conversion funnel](https://us.posthog.com/project/136453/insights/rL5XoyBx?dashboard=459087&variables_override=%7B%7D)
3. [Startup check failures](https://us.posthog.com/project/136453/insights/WIH71rDI?dashboard=459087&variables_override=%7B%7D)
4. [Time to first token (cold start)](https://us.posthog.com/project/136453/insights/p2WmfdO2?dashboard=459087&variables_override=%7B%7D)
5. [Time to first token (warm start)](https://us.posthog.com/project/136453/insights/fk702aRj?dashboard=459087&variables_override=%7B%7D)

These are built on top of either (raw) Events or Actions with different filters and aggregations.

## Major Integration Components

### 1. Consent Filtering

[4 Consent Levels](https://www.notion.so/imbue-ai/Sculptor-telemetry-consent-21fa550faf9580c39913c0997644a2af) are asked for during initial onboarding:
1. ERROR_REPORTING
2. PRODUCT_ANALYTICS
3. LLM_LOGS
4. SESSION_RECORDING

The function `filter_model_by_consent` in `/imbue_core/imbue_core/sculptor/telemetry.py` enforces recursive field-level consent filtering.

Furthermore, all logging to Posthog is done via `emit_posthog_event` which admits `PosthogEventModel` that contains `PosthogEventPayload`-type payload only.

This class enforces all children to specify via Pydantic `json_schema_extra` a "consent_level" set to one of the above levels. Only when the user has supplied a yes to the appropriate level of consent do we emit contents of these fields. The test hooks into Pydantic's `__init__subclass__` function which gets executed at the time of defining children classes. This ensures all data are appropriately filtered as we fail early-on during development when introducing new data models that are logged into Posthog but without consent_level metadata set.

### 2. Session Join-Key Population

A few key attributes are set for every event logged where available:
1. user_id - This is the unique user_id that Posthog associates with a Person
2. instance_id - This is the unique session id that we generate upon starting sculptor each time
3. task_id - This is the ID generated for each Sculptor Task (which also matches the docker container ID)

The `user_id` and `instance_id` are injected each time `emit_posthog_event` is called so the caller doesn't need to worry about shared contextual session IDs. However, `task_id` should be passed into the call as an optional prop as it may not always be present.

### 3. Handling Onboarding Anonymous Users

Initially when a new user starts a sculptor session, they are assigned an anonymous user id that matches the instance_id we generate for each sculptor session (when a user launches `uvx ...`). On the frontend posthog-js sdk, this anonymous user session id is automatically generated.

Crucially, this initially anonymous user_id is NEVER used with the `posthog.identify` call. After user onboards and enters their email, this is used as the key for generating the user's stable distinct user_id. `posthog.alias` is then used to associate the two IDs together as the same Person. After this point, events which are logged prior to onboarding can be sessionized with events logged afterwards, creating a consistent funnel view into the onboarding process.
