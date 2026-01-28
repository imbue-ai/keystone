# Adding events to PostHog

To log a new event to PostHog and get it to show up [here](https://us.posthog.com/project/136453/data-management/events), use the helper function [`emit_posthog_event`](https://gitlab.com/generally-intelligent/generally_intelligent/-/blob/main/imbue_core/imbue_core/sculptor/telemetry.py#L571).

3 things are required to define a new event:

## 1. [SculptorPosthogEvent](https://gitlab.com/generally-intelligent/generally_intelligent/-/blob/main/imbue_core/imbue_core/sculptor/telemetry_constants.py#L36)

- Add a new enum constant into this file: https://gitlab.com/generally-intelligent/generally_intelligent/-/blob/main/imbue_core/imbue_core/sculptor/telemetry_constants.py#L36
- The enum should be formatted as "<component>_<semantic_label>", i.e. "ONBOARDING_EMAIL_CONFIRMATION".

## 2. [PosthogEventPayload](https://gitlab.com/generally-intelligent/generally_intelligent/-/blob/main/imbue_core/imbue_core/sculptor/telemetry.py#L114)

- Define the data contents one wishes to be captured using a PosthogEventPayload data model.
- Ensure all fields are annotated with their corresponding consent levels (using [`with_consent`](https://gitlab.com/generally-intelligent/generally_intelligent/-/blob/main/imbue_core/imbue_core/sculptor/telemetry.py#L89) helper).

## 3. [PosthogEventModel](https://gitlab.com/generally-intelligent/generally_intelligent/-/blob/main/imbue_core/imbue_core/sculptor/telemetry.py#L153)
- Populate default fields as far as possible (given context at the point of logging)
```python
class PosthogEventModel(SerializableModel, Generic[T]):
    """
    Represents a PostHog event, with each field tagged
    with the minimum consent level required for logging.
    """

    # Always defined fields
    name: SculptorPosthogEvent = without_consent(description="Name of event, give it meaning!")
    component: ProductComponent = without_consent(description="App component")

    # User Activity field
    action: UserAction | None = with_consent(ConsentLevel.PRODUCT_ANALYTICS)

    # Task ID - should be set if this event is associated with a task.
    task_id: str | None = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="The task id if this event is task-specific"
    )

    # Payload field with consent level
    payload: T | None = without_consent(description="PostHog Event payload Model")

```
