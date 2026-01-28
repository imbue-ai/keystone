# Posthog SQLs

This directory exists to check into source-control some of our frequently used key metric sql queries. As well as a repository for examples on exploring data in Posthog.

## Insight Creation

To create insights, simply go to one of our dashboards:
1. [Sculptor Product Dashboard](https://us.posthog.com/project/136453/dashboard/459087)
    - Main North-Star metrics!
2. [FUN Sculptor Dashboard](https://us.posthog.com/project/136453/dashboard/509340)
    - For staging interesting exploratory data analysis queries
3. [Sculptor Productionization Dashboard](https://us.posthog.com/project/136453/dashboard/509393)
    - Keeping a pulse on product health metrics
    - As well as release health metrics

## Intermediate Table Materialization

Since some events may arrive with payloads that don't immediately lend to good insight creation (read: denormalized with \<attribute: metric\> rows), we may sometimes require to materialize transformations to disk for large datasets.

It also makes insight creation play nicer with built-in Posthog visualization solutions. Customization options with SQL-based insight is limited, consider materializing a view if it is to be added to one of the above dashboards.

Note that Posthog requires each row to as least have:
1. `distinct_id`
2. `timestamp`

Both of which can be retrieved from the `events` table.
