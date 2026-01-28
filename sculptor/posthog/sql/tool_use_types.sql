-- Number of tool use types
-- This query counts the number of different tool use types, including Bash commands,
-- and aggregates them by their command name.
-- It also handles Bash commands specifically by extracting the command from the invocation string.
-- Link to Insight: https://us.posthog.com/project/136453/insights/dfyPd2jm
-- Note that this query is materialized in the PostHog database.
--   also note that to use it for downstream insight creation, we need to preserve the `distinct_id` and `timestamp` fields.
WITH CONTENT_BLOCKS AS (
    SELECT
      distinct_id,
      timestamp,
      arrayJoin(JSONExtractArrayRaw(properties.payload.content_blocks ?? '[]')) AS content_block
    FROM events
    WHERE event = 'agent_tool_result'
), BASH_TOOLS AS (
    SELECT
        -- This regex extracts the first 'word' (command) from the invocation string.
        -- It's designed to skip a leading 'cd ... &&' pattern to find the actual executable.
        distinct_id,
        timestamp,
        JSONExtractString(content_block, 'invocation_string') AS invocation_string,
        CONCAT('Bash::', extract(invocation_string, '(?:^cd .*? &&\\s+)?(\\w+)')) AS command,
        JSONExtractBool(content_block, 'is_error') AS is_error
    FROM CONTENT_BLOCKS
    WHERE JSONExtractString(content_block, 'tool_name') = 'Bash'
      AND command != '' -- Exclude any empty results
)
SELECT
    distinct_id,
    timestamp,
    JSONExtractString(content_block, 'invocation_string') AS invocation_string,
    JSONExtractString(content_block, 'tool_name') AS command,
    JSONExtractBool(content_block, 'is_error') AS is_error
FROM CONTENT_BLOCKS
WHERE command != 'Bash'
UNION ALL
SELECT * FROM BASH_TOOLS
