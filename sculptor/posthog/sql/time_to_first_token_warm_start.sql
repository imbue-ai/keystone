-- Time to first token warm start
-- This query calculates the time taken for the first token to be generated after a user sends a message
-- within a task that has already been started.
-- TODO: This currently doesn't handle the case where the message is queued.
-- Link to Insight: https://us.posthog.com/project/136453/insights/fk702aRj
WITH
    -- First task_start_message per task_id with message and person
    task_starts AS (
        SELECT
            properties.task_id AS task_id,
            properties.session.instance_id AS instance_id,
            argMin(properties.sculptor_version, created_at) AS sculptor_version,
            argMin(created_at, created_at) AS start_time,
            argMin(JSONExtractRaw(JSONExtractRaw(JSONExtractRaw(properties, 'payload'), 'message'), 'text'), created_at) AS start_message_text,
            argMin(person_id, created_at) AS start_person_id
        FROM events
        WHERE event = 'task_user_message'
          AND properties.task_id IS NOT NULL
          AND properties.session.instance_id IS NOT NULL
        GROUP BY properties.task_id, properties.session.instance_id
    ),

    -- First agent_session_end per task_id
    task_ends AS (
        SELECT
            properties.task_id AS task_id,
            properties.session.instance_id AS instance_id,
            created_at AS end_time
        FROM events
        WHERE event = 'agent_assistant_message'
          AND properties.task_id IS NOT NULL
          AND properties.session.instance_id IS NOT NULL
    ),

    -- Join start + end + email
    task_durations AS (
        SELECT
            starts.task_id,
            starts.instance_id,
            starts.sculptor_version,
            dateDiff('second', starts.start_time, ends.end_time) AS span_duration_seconds,
            persons.properties.email AS user_email
        FROM task_starts AS starts
        INNER JOIN task_ends AS ends
            ON (starts.task_id = ends.task_id AND
                starts.instance_id = ends.instance_id)
        LEFT JOIN persons
            ON persons.id = starts.start_person_id
    ),

    -- Filter by more than 0
    -- This allows us to 'get' the first agent message timestamp AFTER user message timestamp
    -- but I suspect here is where queued messages may result in a 0s-duration span...
    -- Specifically, the end log needs to be the first agent message in response to the user message.
    valid_spans AS (
        SELECT
          user_email,
          task_id,
          instance_id,
          sculptor_version,
          min(span_duration_seconds) AS duration_seconds
        FROM task_durations
        WHERE span_duration_seconds > 0
        GROUP BY user_email, task_id, instance_id, sculptor_version
    )

-- Final aggregation per email
SELECT
    sculptor_version,
    quantile(0.5)(duration_seconds) AS median_duration_seconds,
    quantile(0.95)(duration_seconds) AS p95_duration_seconds,
    quantile(0.99)(duration_seconds) AS p99_duration_seconds
FROM valid_spans
WHERE user_email LIKE '%@%'
  AND user_email != 'test@imbue.com'
GROUP BY sculptor_version
ORDER BY sculptor_version
