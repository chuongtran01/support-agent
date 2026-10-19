# Phase 3: Policy engine and tools

**Status:** planned (not started)
**Gate:** policy tests pass, no LLM

## Goal

Build the part of the system that decides what the agent is allowed to do, and the tools that do it, before any model is involved. By the end, a refund request goes through a policy engine written in plain Python, the four tools (`lookup_order`, `issue_refund`, `create_ticket`, `escalate`) work against a seeded database, and every rule has a test that runs without an LLM.

The evals are the spec: the policy cases are written before the engine they measure, as a table of scenarios with the expected verdict.

## Deliverables

- [ ] **Policy cases**: `evals/datasets/policy.jsonl`. One row per scenario with the facts of the situation and the expected verdict and reason; covers every rule and its boundaries
- [ ] **Schema changes**: Alembic migration `0002`. `order_items.category`, `refunds.order_item_id` and `tickets.kind`
- [ ] **Seed data**: `backend/app/seed.py`. Customers (including an approver), orders, items and refund history that exercise every policy case; relative to the current date so windows stay valid
- [ ] **Policy engine**: `backend/app/policy/`. A pure function that takes a refund request and the facts about it and returns a verdict; no database and no model
- [ ] **Tools**: `backend/app/tools/`. The four tools, each scoped to the session's customer, plus the approver actions
- [ ] **Tool registry**: `backend/app/tools/registry.py`. Name, argument model and handler for each tool, with JSON schemas ready for OpenAI function calling and a `dispatch` function that validates arguments
- [ ] **Tests**: `backend/tests/`. Policy cases, rule boundaries, customer scoping, the approval flow and the refund-limit race

## Policy engine

The numbers come from `data/POLICY_FACTS.md`. The engine is a function of its inputs and the current time, passed in so tests control the clock:

```
evaluate_refund(request, facts, now) -> Verdict
```

- `request`: the order item, the amount in cents and the reason code (`changed_mind`, `defect` or `our_error`).
- `facts`: what the database says about the situation: the customer's membership, the order's `delivered_at`, the item's `is_final_sale`, `category` and price paid, refunds already made on the item, and the customer's refunds in the last 90 days.
- `Verdict`: an outcome (`allowed`, `denied`, `needs_approval` or `manual_review`), a short reason code and a message the agent can pass on to the customer.

### Rules

The checks run in this order, and the first one that fires decides the outcome. Denials come first, then manual review, then the approval gate.

| # | Rule | Outcome | Reason code |
| --- | --- | --- | --- |
| 1 | Order has no `delivered_at` | denied | `not_delivered` |
| 2 | Item category is `gift_card` | denied | `not_refundable` |
| 3 | Reason is `defect` or `our_error` | manual review | `needs_human_review` |
| 4 | Item is final sale | denied | `final_sale` |
| 5 | Delivered more than 30 days ago (60 for Plus members) | denied | `outside_window` |
| 6 | Amount plus earlier refunds on the item exceeds the price paid | denied | `exceeds_item_price` |
| 7 | The customer already has 3 refunds in the last 90 days | manual review | `refund_frequency` |
| 8 | Amount is over $200 | needs approval | `over_threshold` |
| 9 | None of the above | allowed | `ok` |

- **Defects and our errors go to a human.** The policy sheet says defects are covered by warranty regardless of the window or final-sale status, and warranty claims need photos and a review. The engine cannot check a claim, so it never lets a stated reason unlock an exception on its own (rule 3 comes before the final-sale and window checks).
- **Window.** Day 30 after delivery is still inside the window and day 31 is outside, counted in calendar days. The membership tier is read when the request is made.
- **Frequency.** Refunds count if they are `pending`, `approved` or `completed` and were created in the last 90 days. A rejected refund does not count.
- **Threshold.** "Over $200" means strictly more than 20,000 cents; exactly $200 is allowed. The value comes from `REFUND_APPROVAL_THRESHOLD`.
- **Not modeled.** Conditions the data cannot show (unused, original packaging, opened consumables, the tent trial pitch) and the $7.95 return-label fee. The agent handles those from the knowledge base and a ticket.

## Tools

Each tool takes a context holding the database session and the `customer_id`, which comes from the authenticated session and never from the model. The argument models reject unknown fields, so a call that includes a `customer_id` argument is an error.

| Tool | What it does |
| --- | --- |
| `lookup_order(order_id)` | Read-only. Returns the order, its items, delivery date and refunds, if the order belongs to the session's customer. An order that belongs to someone else gets the same "not found" answer as one that does not exist |
| `issue_refund(order_item_id, amount_cents, reason_code)` | Loads the facts, asks the engine, then acts on the verdict (below) |
| `create_ticket(subject, body)` | Opens a support ticket for the session's customer |
| `escalate(reason)` | Opens a ticket of kind `escalation` for a human, for conflicting sources or no answer |

What `issue_refund` does with each verdict:

| Verdict | Result |
| --- | --- |
| `allowed` | Writes a refund with status `completed` |
| `needs_approval` | Writes a refund with status `pending`; the agent tells the customer it is under review |
| `manual_review` | Writes no refund; opens a ticket of kind `manual_review` with the reason |
| `denied` | Writes nothing; returns the reason and message |

The tool locks the customer's row while it counts earlier refunds and inserts the new one, so two refund requests arriving together cannot both slip under the 3-in-90-days limit.

### Approver actions

`decide_refund(approver_id, refund_id, decision)` approves or rejects a `pending` refund, writing `decided_by` and `decided_at`. It checks the approver's role in the database, so it is not a tool the agent can call. It refuses when the caller is not an approver, when the approver is the customer who made the refund, and when the refund is no longer pending. Approval sets the status to `approved` and rejection sets `rejected`; `completed` is for refunds that needed no approval.

### Registry

The registry lists each tool's name, argument model (a Pydantic model, which also gives the JSON schema the model sees) and handler. `dispatch(context, name, raw_arguments)` validates the arguments, calls the handler and returns a result that can be serialized to JSON. Phase 4 plugs the registry into the agent loop.

## Schema changes

Migration `0002` adds what the rules need:

- `order_items.category` (text): `gift_card`, `tent`, `pack`, `footwear`, `apparel`, `consumable`, `other`. Needed for the gift card rule, and later for warranty length (packs get 3 years)
- `refunds.order_item_id` (references `order_items`): refunds are per item, so the engine can cap the total against the price paid
- `tickets.kind` (text, default `support`): `support`, `escalation` or `manual_review`

`refunds.reason` stores the reason code.

## Seed data

`python -m app.seed` clears and reloads the business tables, so it can be run again at any time. Dates are written relative to now. The set includes:

- A Basic customer, a Plus customer and an approver.
- Orders delivered 5, 29, 30, 31, 45 and 61 days ago, which cover both window boundaries.
- Items that are final sale, gift cards, tents, packs, and priced under and over $200.
- Refund history for one customer with 3 refunds in the last 90 days and one whose oldest refund is 91 days old.

Passwords are a placeholder until phase 4 adds login; the seed can be re-run then.

## Tests

- **Policy cases** (no database): every row of `policy.jsonl` is run through `evaluate_refund`, and the verdict and reason must match.
- **Boundaries** (no database): day 30 against day 31, 60 against 61 for Plus, $200.00 against $200.01, the third refund against the fourth, a refund exactly 90 days old, and the rule order (a defect claim on a final-sale item still goes to manual review).
- **Scoping** (database): a customer cannot read, refund or ticket against another customer's order; unknown and foreign orders look the same; a `customer_id` argument is rejected.
- **Approval flow** (database): the verdict-to-row mapping above, then approve, reject, approving your own refund, approving as a non-approver, and deciding twice.
- **Concurrency** (database): two simultaneous refunds at the limit produce one refund and one manual review.

The policy tests need no database or API key. The tool tests need Postgres, so they run against a separate test database on the Compose container and are skipped when it is not reachable.

## Build order

- [ ] 1. Write the policy cases, including boundaries, and review them.
- [ ] 2. Migration `0002` and the seed script.
- [ ] 3. The policy engine, until all cases pass.
- [ ] 4. The tools and `decide_refund`, with the scoping and approval tests.
- [ ] 5. The registry and `dispatch`.
- [ ] 6. The concurrency test and a pass over the rules against `POLICY_FACTS.md`.

## Gate check

- [ ] **Policy engine**: every policy case passes
- [ ] **Boundaries**: window, threshold and frequency edges are tested
- [ ] **Scoping**: no tool reads or changes another customer's data
- [ ] **Approval flow**: approver rules are tested
- [ ] **Seed data**: loads on a fresh database and can be re-run
- [ ] **No LLM**: the whole phase runs without an API key

## Decisions to confirm

- **A fourth verdict.** `DESIGN.md` lists three verdicts (allowed, needs approval, manual review), but refusing a final-sale or out-of-window refund is a fourth outcome, so this plan adds `denied`.
- **Defect and our-error claims always go to a human.** The alternative is to let the engine allow them, which lets the model unlock the window and final-sale exceptions just by saying "defect".
- **Approval status.** Approved refunds are `approved` and refunds that needed no approval are `completed`, since no payment is simulated.
- **Calendar days for the window.** The other choice is exact 24-hour periods from the delivery timestamp.
- **Tool structure.** The tools are plain functions behind a registry. A shared base class could force every tool to call the engine, but only `issue_refund` needs it in this phase.
- **Database tests in the gate.** The policy tests run anywhere, and the tool tests need the Postgres container. `PHASE2.md` required `pytest` to pass with no database; this phase relaxes that for the tool tests only.

## Not in this phase

- Login, sessions and the HTTP endpoints (phase 4)
- The agent loop, tool-calling prompts and when to escalate (phase 4)
- The approval page (phase 5) and the approver API it calls (phase 4)
- Tool-call accuracy and trap evals that need a model (phase 4)
- Prompt-injection handling of tool output (phase 4 and the phase 6 injection suite)
