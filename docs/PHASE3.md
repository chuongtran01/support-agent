# Phase 3: Policy engine and tools

**Status:** in progress (steps 1 to 4 of 6 done)
**Gate:** policy tests pass, no LLM

## Goal

Build the part of the system that decides what the agent is allowed to do, and the tools that do it, before any model is involved. By the end, a refund request goes through a policy engine written in plain Python, the four tools (`lookup_order`, `issue_refund`, `create_ticket`, `escalate`) work against a seeded database, and every rule has a test that runs without an LLM.

The evals are the spec: the policy cases are written before the engine they measure, as a table of scenarios with the expected verdict.

## Deliverables

- [x] **Policy cases**: `evals/datasets/policy.jsonl`. 45 scenarios, each with the facts of the situation and the expected verdict and reason; covers every rule, its boundaries and the order the rules are checked in
- [x] **Schema changes**: Alembic migration `0002`. `order_items.category`, `refunds.order_item_id`, `refunds.quantity` and `tickets.kind`
- [x] **Seed data**: `backend/app/seed.py` (`make seed`). 6 customers (including an approver), 25 orders and 8 refunds that exercise the window, frequency and approval rules; dates are relative to now so the boundaries stay in place
- [x] **Policy engine**: `backend/app/policy/engine.py`. A pure function that takes a refund request and the facts about it and returns a verdict; no database and no model. All 45 policy cases pass
- [x] **Tools**: `backend/app/tools/`. The four tools, each scoped to the session's customer, plus `decide_refund` for approvers (in `refunds.py`, not callable by the agent)
- [ ] **Tool registry**: `backend/app/tools/registry.py`. Name, argument model and handler for each tool, with JSON schemas ready for OpenAI function calling and a `dispatch` function that validates arguments
- [ ] **Tests**: `backend/tests/`. Policy cases, rule boundaries, customer scoping, the approval flow and the refund-limit race

## Policy engine

The numbers come from `data/POLICY_FACTS.md`. The engine is a function of its inputs and the current time, passed in so tests control the clock:

```
evaluate_refund(request, facts, now) -> Verdict
```

- `request`: the order item, the number of units being returned and the reason code (`changed_mind`, `defect` or `our_error`). There is no amount: the model never chooses how much money moves.
- `facts`: what the database says about the situation: the customer's membership, the order's `delivered_at`, the item's `is_final_sale`, `category`, price paid per unit, and the date of each of the customer's earlier refunds that count. By the time the engine runs, the request has already been checked as valid (see Tools), so it never has to decide whether the quantity makes sense.
- `Verdict`: an outcome (`allowed`, `denied`, `needs_approval` or `manual_review`), a short reason code, a message the agent can pass on to the customer, and the refund amount (price per unit times quantity) so the tool writes the figure the engine checked.
- `PolicyConfig`: the numbers the rules use (windows, frequency limit, approval threshold), in one place. The tool layer builds it from `REFUND_APPROVAL_THRESHOLD`.

### Rules

The checks run in this order, and the first one that fires decides the outcome.

| # | Rule | Outcome | Reason code |
| --- | --- | --- | --- |
| 1 | Order has no `delivered_at` | denied | `not_delivered` |
| 2 | Item category is `gift_card` | denied | `not_refundable` |
| 3 | Reason is `defect` or `our_error` | manual review | `needs_human_review` |
| 4 | Item is final sale | denied | `final_sale` |
| 5 | Delivered more than 30 days ago (60 for Plus members) | denied | `outside_window` |
| 6 | The customer already has 3 refunds in the last 90 days | manual review | `refund_frequency` |
| 7 | Refund amount is over $200 | needs approval | `over_threshold` |
| 8 | None of the above | allowed | `ok` |

- **Defects and our errors go to a human.** The policy sheet says defects are covered by warranty regardless of the window or final-sale status, and warranty claims need photos and a review. The engine cannot check a claim, so it never lets a stated reason unlock an exception on its own (rule 3 comes before the final-sale and window checks).
- **Window.** Day 30 after delivery is still inside the window and day 31 is outside, counted in calendar days in UTC (so a delivery at 11:59 p.m. and a request at 12:01 a.m. 30 days later is day 30). The engine rejects times without a timezone. The membership tier is read when the request is made.
- **Frequency.** Refunds count if they are `pending`, `approved` or `completed` and were made 89 days ago or less; one made 90 days ago no longer counts. A rejected refund does not count. The engine receives the date of each countable refund and works out the age itself, so the boundary is tested without a database.
- **Threshold.** "Over $200" means strictly more than 20,000 cents; exactly $200 is allowed. The value comes from `REFUND_APPROVAL_THRESHOLD`.
- **The amount comes from the order.** The engine computes it as the price paid per unit times the units returned. The price paid is `order_items.price_cents`, which is saved on the order line when the order is placed, so it is what the customer paid and not today's price. A partial refund is a smaller number of units, and a refund of part of one unit's price (a goodwill discount) is not automatic, so the agent opens a ticket for it.
- **Not modeled.** Conditions the data cannot show (unused, original packaging, opened consumables, the tent trial pitch); the $7.95 return-label fee; discounts, so `price_cents` must hold the net price paid; sales tax; and shipping charges, which are refunded only for defects and our errors, and those already go to a human. The agent handles the first group from the knowledge base and a ticket.

## Policy cases

`evals/datasets/policy.jsonl` has one JSON object per line, and every row has the same fourteen fields:

| Field | Meaning |
| --- | --- |
| `id`, `group`, `description` | Row id (`p001` and up), the rule group it belongs to, and what the case shows |
| `membership` | `none`, `basic` or `plus` |
| `delivered_days_ago` | Days since delivery, or null for an order that has not been delivered |
| `item_category` | `gift_card` or `other`; the migration adds the full list of categories |
| `is_final_sale` | Whether the item was bought as final sale |
| `unit_price_cents` | Price paid per unit |
| `refund_ages_days` | Age in days of each earlier refund that is `pending`, `approved` or `completed`, including ones older than 90 days |
| `reason_code` | `changed_mind`, `defect` or `our_error` |
| `quantity` | Units the customer wants to return, already checked as valid; the amount is this times `unit_price_cents` |
| `expected_outcome`, `expected_reason` | `allowed`, `denied`, `needs_approval` or `manual_review`, and the reason code from the rule table |
| `note` | Extra explanation, null by default |

The 45 rows are grouped as: happy path 5, delivery 2, window 10, gift card 3, human review 6, final sale 4, frequency 7, threshold 5 and rule order 3. Every reason code has at least two rows. The rule order rows put two rules in conflict, for example a final-sale item outside the window, so the first rule in the table has to win.

The test turns the days-ago values into timestamps relative to a fixed clock and passes them to the engine, which works out the ages itself.

The expected values were cross-checked against a separate throwaway implementation of the rule table before the engine existed, and no row disagreed.

## Tools

Each tool takes a context holding the database session and the `customer_id`, which comes from the authenticated session and never from the model. The argument models reject unknown fields, so a call that includes a `customer_id` argument is an error.

| Tool | What it does |
| --- | --- |
| `lookup_order(order_id)` | Read-only. Returns the order, its items, delivery date and refunds, if the order belongs to the session's customer. An order that belongs to someone else gets the same "not found" answer as one that does not exist |
| `issue_refund(order_item_id, quantity, reason_code)` | Loads the facts, asks the engine, then acts on the verdict (below). The amount is worked out in code, never passed in |
| `create_ticket(subject, body)` | Opens a support ticket for the session's customer |
| `escalate(reason)` | Opens a ticket of kind `escalation` for a human, for conflicting sources or no answer |

### Checks before the engine

Whether a request makes sense is separate from whether policy allows it, so `issue_refund` checks the request first, and the engine only sees valid ones:

1. **Arguments.** `quantity` is a whole number of at least 1, the reason code is one of the three, and no other fields are present.
2. **The item.** It exists and belongs to an order of the session's customer. Anything else gets the same "not found" answer.
3. **The quantity.** It is no more than the units still refundable: units bought, minus units in earlier refunds that are `pending`, `approved` or `completed`. This is a small pure function, `check_quantity(requested, bought, refunded)`, so it is tested without a database.

A failed check writes nothing and returns an `invalid_request` error that says what to fix, such as "only 1 unit can still be refunded", so the model can correct itself. It is not a policy verdict.

What `issue_refund` does with each verdict:

| Verdict | Result |
| --- | --- |
| `allowed` | Writes a refund (amount, quantity, reason code) with status `completed` |
| `needs_approval` | Writes a refund with status `pending`; the agent tells the customer it is under review |
| `manual_review` | Writes no refund; opens a ticket of kind `manual_review` with the reason |
| `denied` | Writes nothing; returns the reason and message |

The tool locks the customer's row (`SELECT ... FOR UPDATE`) while it counts earlier refunds and inserts the new one, so two refund requests arriving together cannot both slip under the 3-in-90-days limit. The concurrency test fails without the lock and passes with it.

**Conventions.** Tools are async functions that take a `ToolContext` (the database session, the session's `customer_id` and a clock) followed by the model's arguments. They run inside the caller's transaction and never commit: the dispatcher opens one transaction per call, so a tool that fails halfway writes nothing. Every tool returns a typed Pydantic result from `app/tools/results.py` with a `status` from a fixed set; the registry turns it into JSON for the model. `issue_refund` uses the verdict (`allowed`, `denied`, `needs_approval`, `manual_review`) or `not_found` or `invalid_request`, and also returns the reason, the message, the amount, and the refund or ticket id if one was written. Writes use the context's clock, so tests control time.

### Approver actions

`decide_refund(approver_id, refund_id, decision)` approves or rejects a `pending` refund, writing `decided_by` and `decided_at`. It checks the approver's role in the database, so it is not a tool the agent can call. It refuses when the caller is not an approver, when the approver is the customer who made the refund, and when the refund is no longer pending. Approval sets the status to `approved` and rejection sets `rejected`; `completed` is for refunds that needed no approval.

### Registry

The registry lists each tool's name, argument model (a Pydantic model, which also gives the JSON schema the model sees) and handler. `dispatch(context, name, raw_arguments)` validates the arguments, calls the handler and returns a result that can be serialized to JSON. Phase 4 plugs the registry into the agent loop.

## Schema changes

Migration `0002` adds what the rules need:

- `order_items.category` (text): `gift_card`, `tent`, `pack`, `footwear`, `apparel`, `consumable`, `other`. Needed for the gift card rule, and later for warranty length (packs get 3 years)
- `refunds.order_item_id` (references `order_items`) and `refunds.quantity`: refunds are per item, so the engine can count the units already refunded
- `tickets.kind` (text, default `support`): `support`, `escalation` or `manual_review`

`refunds.reason` stores the reason code.

## Seed data

`python -m app.seed` (or `make seed`) truncates the business tables (customers, orders, items, refunds, tickets and chat data) and reloads them, so it can be run again at any time and gives the same ids. The knowledge base is untouched. Dates are written relative to now. The set:

| Customer | Membership | What their data is for |
| --- | --- | --- |
| Alex | basic | Orders delivered 5, 29, 30, 31 and 45 days ago (the 30-day boundary); a shipped order; a final-sale item; a gift card; a three-unit line with one unit already refunded |
| Priya | plus | Orders delivered 45, 60 and 61 days ago (the 60-day boundary); items over $200 (tent, pack, parka); a pending $240 refund waiting for an approver |
| Sam | none | A consumable delivered 30 days ago, a stove delivered 31 days ago, an order still processing |
| Jordan | basic | Three completed refunds 5, 30 and 60 days ago, so the next request goes to manual review |
| Morgan | basic | Three completed refunds 20, 45 and 91 days ago, so only two count and the next request is allowed |
| Taylor | approver | The approver account |

Passwords are a placeholder until phase 4 adds login; the seed can be re-run then.

## Tests

- **Policy cases** (no database): every row of `policy.jsonl` is run through `evaluate_refund`, and the verdict and reason must match.
- **Request checks** (no database): `check_quantity` with the full line, a partial return, units already refunded, rejected refunds not counting, one unit too many and a line already refunded in full.
- **Boundaries** (no database): day 30 against day 31, 60 against 61 for Plus, $200.00 against $200.01, the third refund against the fourth, a refund exactly 90 days old, and the rule order (a defect claim on a final-sale item still goes to manual review).
- **Scoping** (database): a customer cannot read, refund or ticket against another customer's order; unknown and foreign orders look the same; a `customer_id` argument is rejected.
- **Approval flow** (database): the verdict-to-row mapping above, then approve, reject, approving your own refund, approving as a non-approver, and deciding twice.
- **Concurrency** (database): two simultaneous refunds at the limit produce one refund and one manual review.

The policy tests need no database or API key. The tool tests need Postgres, so they run against a separate test database on the Compose container and are skipped when it is not reachable (`tests/conftest.py` builds a `support_test` database with the real migrations and empties it after each test). The seed has its own database tests.

## Build order

- [x] 1. Write the policy cases, including boundaries, and review them.
- [x] 2. Migration `0002` and the seed script.
- [x] 3. The policy engine, until all cases pass.
- [x] 4. The tools and `decide_refund`, with the scoping and approval tests.
- [ ] 5. The registry and `dispatch`.
- [ ] 6. The concurrency test and a pass over the rules against `POLICY_FACTS.md`. The concurrency test is written; the pass against the fact sheet is not.

## Gate check

- [x] **Policy engine**: every policy case passes
- [x] **Boundaries**: window, threshold and frequency edges are tested
- [x] **Scoping**: no tool reads or changes another customer's data
- [x] **Approval flow**: approver rules are tested
- [x] **Seed data**: loads on a fresh database and can be re-run
- [ ] **No LLM**: the whole phase runs without an API key

## Decisions to confirm

- **A fourth verdict.** `DESIGN.md` lists three verdicts (allowed, needs approval, manual review), but refusing a final-sale or out-of-window refund is a fourth outcome, so this plan adds `denied`.
- **Defect and our-error claims always go to a human.** The alternative is to let the engine allow them, which lets the model unlock the window and final-sale exceptions just by saying "defect".
- **Approval status.** Approved refunds are `approved` and refunds that needed no approval are `completed`, since no payment is simulated.
- **Quantity is a request check, not a policy rule.** Returning more units than were bought is invalid whatever the company's policy says, so it is checked before the engine and reported as `invalid_request`, not `denied`.
- **The model never passes an amount.** `issue_refund` takes a quantity and the code works out the money from the price paid. The cost is that a partial refund of part of one unit's price needs a human.
- **Calendar days for the window.** The other choice is exact 24-hour periods from the delivery timestamp.
- **Tool structure.** The tools are plain functions behind a registry. A shared base class could force every tool to call the engine, but only `issue_refund` needs it in this phase.
- **Database tests in the gate.** The policy tests run anywhere, and the tool tests need the Postgres container. `PHASE2.md` required `pytest` to pass with no database; this phase relaxes that for the tool tests only.

## Not in this phase

- Login, sessions and the HTTP endpoints (phase 4)
- The agent loop, tool-calling prompts and when to escalate (phase 4)
- The approval page (phase 5) and the approver API it calls (phase 4)
- Tool-call accuracy and trap evals that need a model (phase 4)
- Prompt-injection handling of tool output (phase 4 and the phase 6 injection suite)
