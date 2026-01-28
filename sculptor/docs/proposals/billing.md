# Billing

This document proposes the initial billing setup from the technical perspective.

It's a living document and is expected to change as we learn more about the options and constraints we're facing.

## Requirements

To start with, we're going to focus on the Pro tier from the [Sculptor pricing document](https://www.notion.so/imbue-ai/Sculptor-Pricing-240a550faf95804c8e45e24a039237c6),
with the ability to buy Power User Credit Packs, without credit rollover expiration.

Also, we will only deal with LLM API usage tracking for now (i.e. not Modal or anything else).

## Lago setup

We intend to use [Lago](https://getlago.com/) as a billing provider in the cloud. Within the Lago platform, we will:

- Create one `Customer` per Sculptor organization.
- Create multiple `Billable metrics`, for example:
    - `claude_sonnet_4_1_input_tokens`
    - `claude_sonnet_4_1_output_tokens`
    - ...
    - (That way we don't need to use "filters" which gives us some flexibility with wallet setups.)
- Create a single `Plan` with the $25 base fee with a monthly billing period and a `Standard` charge model.
    - The subscription base fee will be paid in advance.
    - Charges connected to the billable metrics will be paid in arrears (to avoid generating an invoice with each single billing event).
- Create a `Wallet` for each customer.
    - Automatically top up the wallet by $10 every time the customer pays their subscription.
    - Additionally top up the wallet when customers buy credit packs.
- Use the `Customer Portal` to let users see past invoices, usage, current balance and top up their wallets.
- Use the Stripe integration for payment processing.


## Integration

All the Lago-related automation will be done via `imbue_gateway`.

- A `Customer` and a `Subscription` will be created on-demand, via a user's click inside Sculptor ("Subscribe").
- We will cache customer's wallet balance in Redis.
- The cache will be updated lazily from the authoritative source (Lago) when balance is actually checked.
- Aside from that, we will also configure Lago to send us the following events via webhooks to update the cache:
    - `invoice.paid_credit_added` and / or `invoice.payment_status_updated` when funds become available.
    - `wallet.depleted_ongoing_balance` when customer runs out of funds.
- Sculptor users can only send LLM requests through `imbue_gateway` if their organization's balance is positive.
- Upon each LLM request and response, a billing `Event` will be sent to Lago via their http endpoint.
- Later on, we'll use a Redis-based custom ingestion connector to Lago to publish billing events, too, because that has a significantly higher throughput than the HTTP interface.

There will be a new modal / page inside Sculptor with Organization information. That's where the "subscribe" button will be located.
We can also use that section to embed the [Customer portal](https://getlago.com/docs/guide/customers/customer-portal).


## Single users vs team plans

The above assumes single-user subscriptions only.

In the future, when we implement Team plans, we will need to:

- Represent the number of seats as a Recurrent billable metric.
- Rethink the free usage quota.
- Validate that Pro plans are only activated for personal (=single-user) organizations.


## Open questions

- Are we OK with foregoing rollover credit expiration for now?
- How should we set up the tax rate / tax codes in Lago?
- When a client has still balance in credits but doesn't pay the monthly subscription, how quickly do we want to cut their access?
- Are we OK with users getting to small negative ballance territory when they run out of credits before we cut their access?
