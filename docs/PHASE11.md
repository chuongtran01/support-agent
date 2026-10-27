# Phase 11: Idempotency keys and a safe retry

**Status:** planned
**Gate:** the same write call sent twice with the same key is done once and gives the same answer both times; a call whose answer is lost is retried once and, if the retry reaches the server, ends with the real result instead of `unknown`; a key can never reach another customer's data

This is a follow-on to `MCP_DESIGN.md`, which listed idempotency keys as "a possible later change". Phase 9 made an unknown outcome honest. This phase makes most of them go away. It is done **before phase 10**, so the evals measure the client as it will stay.

## Goal

When `McpBackend` sends a write and the answer is lost, the step may be done. Phase 9 reports that and stops, because retrying without protection could refund twice. A key removes the danger: the client sends the same call again with the same key, and the server either runs it for the first time or returns what the first attempt produced. Either way the step happens once and the client learns the outcome.

By the end, `issue_refund`, `create_ticket` and `escalate` accept a key that the client makes and the model never sees, the server remembers the result under it, and `McpBackend` retries once after an `unknown`.

## What this does not fix

A key identifies one call. If the model decides, after an `unknown`, to call `issue_refund` again, that is a new call with a new key and it is treated as a new request. The defenses there stay what they are: the unknown text tells the model not to repeat the step, and the refund's unit check stops a second refund of a whole line. A multi-unit line could still be refunded twice that way. Closing that would take a different rule (for example, refusing the same customer, item, quantity and reason inside a short window), and is not in this phase. It is said in the README as a known limit.

## Deliverables

- [x] **Table**: `idempotency_keys`, with a unique key per customer, made by migration `0004`
- [ ] **Key in the context**: `ToolContext` carries an optional key, set by the server from the request and never from the tool's arguments
- [ ] **Replay**: a tool marked idempotent runs once per customer and key; a second call with the same key and the same request returns the first call's output exactly, and a second call with a different request is refused
- [ ] **Tools**: `issue_refund`, `create_ticket` and `escalate` are marked idempotent
- [ ] **Transport**: the key travels with the call over MCP and the server reads it
- [ ] **Client**: `McpBackend` makes one key per call and retries once after an `unknown` when that is safe
- [ ] **Tests**: replay, refusal, concurrency, isolation between customers, rollback, the transport, and the retry
- [ ] **Real run**: a real server whose first answer is lost, the retry returning the stored result, one row in the database, written up under "Gate check"

## What the earlier phases already give

- `run_tool` already opens one transaction per call and rolls back on failure, so a key row written inside it is kept only if the tool succeeded. A tool that fails leaves no key behind and a retry runs it fresh, which is right
- `ToolContext` is built in one place per call on the server and in the tests, so a new field with a default changes none of them
- `issue_refund` already takes a lock on the customer row, so two refunds for one customer cannot interleave
- The tool's output is one `ToolOutput(text, status, receipt)`. Storing those three values is enough to answer a repeat exactly, receipt included
- The server already reads the request's headers for authentication, and the SDK carries request metadata (`_meta`) on `tools/call`, which accepts any extra key (checked on the installed version)
- `McpBackend` already knows whether a failure happened before the request that runs the tool was sent, and whether the tool changes data (`ToolSpec.action`)
- The loop never retries a tool. That stays: this retry is inside `McpBackend`, one level down, and the loop sees only the final output

## The table

`idempotency_keys`:

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | integer, primary key | |
| `customer_id` | integer, foreign key to `customers` | Whose key this is |
| `key` | text | What the client sent |
| `tool` | text | The tool the first call ran |
| `fingerprint` | text | A hash of the tool name and its validated arguments |
| `output` | text | The first call's `ToolOutput.text` |
| `status` | text | Its status |
| `receipt` | text, nullable | Its receipt; empty for a denial or a read |
| `created_at` | timestamp | When it was stored, so old keys can be removed later |

A unique index on `(customer_id, key)` is the rule that one key means one call. The customer is part of it so a key made for one customer can never match another's, whatever a client sends.

**Why a table and not a column on `refunds`.** A column can say that a refund exists, but not what the first answer was: the message, the reason and any ticket opened are not stored on the refund. A denial writes no refund at all, and a request that goes to manual review opens a ticket, so a repeat would open a second one. Storing the first output answers every repeat the same way, for every outcome, and works for the other write tools without more columns. The `receipt` column is nullable because most outputs have none.

`idempotency_keys` is added to the list of business tables that the seed and the test fixtures clear.

## The mechanism

Inside the transaction `run_tool` already opens, for a tool marked idempotent and a call that carries a key:

1. Validate the arguments, as now, and work out the fingerprint
2. Insert a row for `(customer_id, key)` with `ON CONFLICT DO NOTHING`
3. If the insert made a row, this is the first call: run the tool, then fill in the row with its output, status and receipt, and commit them together
4. If it did not, another call holds the key. Read that row. If its fingerprint differs, return `invalid_request` ("this key was used for a different request"). Otherwise return its stored output as the result and run nothing

Postgres makes a second insert for the same key wait until the first transaction commits or rolls back, so two identical requests arriving together give one run and one replay, with no extra lock. If the first rolls back, the second becomes the first call.

A call with no key, or to a tool that is not marked, runs exactly as today. `LocalBackend` sends no key: there is no network between the loop and the tool to lose an answer in.

**What is stored.** Every outcome of a call that reached the tool, including denials and manual reviews, because a repeat of any of them must give the same answer and open no second ticket. Calls turned away before the tool runs (an unknown tool, invalid arguments) store nothing. They change nothing, so a repeat is harmless.

**Keys from a client.** A key longer than 200 characters, or empty, is `invalid_request`. Anything else is accepted as text. It is scoped to the customer in the token, so the worst a client can do with a chosen key is replay or block its own calls.

## The transport

The key travels in the request's metadata, under `northwind/idempotency-key`, the same style as the status and receipt in a result's `_meta`. The client passes it with the call, and the server's handler reads it from the request parameters and puts it in `ToolContext`. It is never an argument: the argument models reject unknown fields, so a model that writes a key into its arguments is turned away, and the model is not told a key exists.

## The retry

`McpBackend.run` makes one key at the start, a random UUID, and holds it in a local variable. After an attempt that ends `unknown`, it tries once more with the same key if the call is safe to repeat: a tool that only reads, or one marked idempotent. A write that is not marked is never retried.

- The retry is a new connection and a new short-lived token, with the same key
- If the first attempt is still running on the server, the retry waits on the key and then replays; if the first one did commit, the retry replays it; if neither did anything, the retry runs it
- If the retry also ends `unknown`, the result is `unknown`, as now, and the loop's messages for it from phase 9 apply
- Each attempt has the full call timeout, so one call can take up to twice `MCP_CALL_TIMEOUT_SECONDS`. That is noted in the settings comment and is the cost of the retry
- A failure before the request that runs the tool was sent is still a plain `error` ("not done"), and is not retried: nothing was sent, and the model can decide what to do

The key lives only inside one `run()`. If the agent process dies between the attempts, nothing retries and the step stays unknown, which is no worse than before.

## Tests

| Test | Checks |
| --- | --- |
| Migration | The table exists after upgrade and is gone after downgrade; the unique index refuses a second row for the same customer and key and allows the same key for another customer |
| Replay | A refund called twice with one key makes one refund and gives the same text, status and receipt both times |
| Every outcome | A denied refund, a refund held for approval, and a manual review each replay the first answer, and the manual review opens one ticket |
| Different request | The same key with different arguments or a different tool gives `invalid_request` and runs nothing |
| No key | Two calls with no key behave as before, and `LocalBackend` sends none |
| Concurrency | Two identical calls at once give one run and one replay |
| Rollback | A tool that raises leaves no key behind, so the retry runs it |
| Isolation | Customer A's key does not replay for customer B, and cannot be read through B |
| Bad keys | An empty key and one over 200 characters are refused |
| Model cannot send one | A key written in the tool's arguments is rejected, and no schema the model sees mentions it |
| Transport | A key sent in the request's metadata reaches `ToolContext` over the real app, with a real token |
| Retry, lost answer | A server that commits and then stalls on the first request only: the client's first attempt is `unknown`, the retry returns the first result, and the database holds one row |
| Retry, nothing happened | A first attempt that never reached the tool is retried and runs once |
| No retry | A write that is not marked is not retried; a failure before sending is not retried; two failed attempts give `unknown` |
| Phase 9 and 8 tests | Pass unchanged |

Each of the main guarantees gets a deliberate break to show its test fails: the lookup removed (a repeat refunds twice), the customer left out of the index (a key matches across customers), and the retry sending a new key (the same repeat refunds twice).

## Build order

1. The migration, the model and the table in the cleared list, with the index test
2. `ToolContext` gets the key, `ToolSpec` gets the flag, and `run_tool` does the claim and replay; tests with a key set directly on the context
3. The three tools marked idempotent, and the tests for every outcome, refusal, concurrency, rollback and isolation
4. The key over MCP: the client sends it, the server reads it, and the transport tests
5. `McpBackend` makes a key and retries once; the retry tests, with the deliberate breaks
6. The real run with a server whose first answer is lost, written into this file

## Gate check

To fill in when the phase is done.

## Decisions to confirm

- **A table, not a column on `refunds`.** This replaces the column I described when the idea first came up. A column cannot give the same answer again for a denial or a manual review, and cannot reproduce the first message. The table does, and covers the other tools. The cost is one more table and one migration
- **Which tools.** Proposed: `issue_refund`, `create_ticket` and `escalate`, because the mechanism is the same for all three and each is one flag. The refund is the one that matters. The alternative is the refund only, which leaves two tools without a safe retry
- **Metadata or a header for the key.** Proposed: request metadata, because it is per call and mirrors how the result's status travels back. A header is simpler on the server, but it belongs to the connection, and the connection is per call only by our own choice
- **Retry once, not more, and not for unmarked writes.** Proposed. More retries stretch the worst case further, and one covers a dropped answer
- **Keep stored keys forever for now.** Proposed, with `created_at` so a cleanup can be added. A job to remove old keys is not part of this phase
- **Replay stores the first answer, not a fresh one.** A repeat of a refund that was held for approval and has since been approved still says "waiting for approval". Proposed: that is the answer to the call as it was made, and the customer can ask again for the current state

## Not in this phase

- A rule against the model repeating a request with a new key
- Removing old keys
- Keys for the loop's own hand-over ticket, which is not a tool call and does not go over MCP
- The agent evals on both backends and the latency numbers (phase 10)
- Pooling or reusing connections
- Moving the tool code out of the backend package
