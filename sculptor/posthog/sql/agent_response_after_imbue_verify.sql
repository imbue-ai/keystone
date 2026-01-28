-- Query to generate content blocks from agent responses after imbue verify
-- This query retrieves content blocks from agent responses after the imbue verify tool is called.
WITH ASSISTANT_MESSAGE_CONTENT_BLOCKS AS (
    SELECT
      timestamp,
      properties.task_id AS task_id,
      properties.session.instance_id AS instance_id,
      arrayJoin(JSONExtractArrayRaw(properties.payload.content_blocks ?? '[]')) AS content_block
    FROM events
    WHERE event = 'agent_assistant_message'
), TOOL_RESULT_CONTENT_BLOCKS AS (
    SELECT
      timestamp,
      properties.task_id AS task_id,
      properties.session.instance_id AS instance_id,
      arrayJoin(JSONExtractArrayRaw(properties.payload.content_blocks ?? '[]')) AS content_block
    FROM events
    WHERE event = 'agent_tool_result'
), CONTENT_BLOCKS AS (
   SELECT * FROM ASSISTANT_MESSAGE_CONTENT_BLOCKS
   UNION ALL
   SELECT * FROM TOOL_RESULT_CONTENT_BLOCKS
), IMBUE_VERIFY_CALLED_SESSIONS AS (
    SELECT DISTINCT
      timestamp,
      properties.task_id AS task_id,
      arrayJoin(JSONExtractArrayRaw(properties.payload.content_blocks ?? '[]')) AS content_block
    FROM events
    WHERE event = 'agent_tool_result'
      AND JSONExtractString(content_block, 'tool_name') = 'mcp__imbue__verify'
)
SELECT
  verify.task_id AS task_id,
  content.instance_id AS instance_id,
  JSONExtractString(verify.content_block, 'tool_use_id') AS tool_use_id,
  JSONExtractBool(verify.content_block, 'is_error') AS is_error,
  content.content_block AS content_block,
  verify.content_block AS imbue_verify_result
FROM IMBUE_VERIFY_CALLED_SESSIONS AS verify
LEFT JOIN CONTENT_BLOCKS AS content
ON verify.task_id = content.task_id
WHERE content.timestamp > verify.timestamp
