-- Number of failing onboarding sessions
-- This query calculates the number of onboarding sessions that failed checks
-- and the percentage of failures per day.
-- Link to Insight: https://us.posthog.com/project/136453/insights/4O2u73VR
WITH TBL_CHECKS AS (
    SELECT
        arraySum(x -> NOT JSONExtractBool(x, 'passed'), JSONExtractArrayRaw(properties.payload.results ?? '[]')) as num_failed,
        timestamp as timestamp
    FROM
        events
    WHERE
        event = 'onboarding_startup_checks'
        AND properties.payload IS NOT NULL
), DAILY_COUNT AS (
    SELECT
    toStartOfDay(timestamp) as day,
    num_failed = 0 as is_successful,
    count() as cnt
    FROM TBL_CHECKS
    GROUP BY day, is_successful
)
SELECT
    toDate(day),
    sumIf(cnt, is_successful = 0) AS failed_checks,
    sum(cnt) AS total_checks,
    round((failed_checks / total_checks) * 100, 4) AS failure_percentage
FROM
    DAILY_COUNT
GROUP BY day
ORDER BY day
