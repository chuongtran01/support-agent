# Phase 9: When a tool call fails or stalls

**Status:** planned
**Gate:** a call that stalls after the server committed gives `unknown` and never "nothing was changed"; a call that never left the agent still says it was not done; a turn that ends after an unknown step tells the customer so

This is phase 3 of `MCP_DESIGN.md`. Phase 8 made the agent call the tools over MCP and gave every transport failure one cautious answer. This phase replaces that stand-in with the real outcomes, and puts a limit on how long a call may take.

## Goal

Over a network, "the call failed" can mean two different things. The request may never have reached the server, in which case nothing was done. Or it may have reached it, the tool may have committed, and the answer may have been lost or late, in which case the step may be done. `run_tool` can say "nothing was changed" because it rolls back on failure. A client that lost the answer cannot say that.

By the end, `McpBackend` tells these apart, gives the slow case a time limit, and the loop reports an unknown step to the customer in words that are true. `local` is unchanged, and `TOOL_BACKEND` stays `local` by default until the evals in phase 10 are run.

## Deliverables

- [x] **Timeout**: `MCP_CALL_TIMEOUT_SECONDS` in the settings and `.env.example`; every MCP call is bounded by it
- [x] **Outcomes**: `McpBackend` returns `error` when the call never left the agent, and `unknown` when it may have reached the server
- [x] **Which steps matter**: each tool that changes something has a customer-facing name, so an unknown result can say what could not be confirmed
- [ ] **Loop**: an unknown step is kept apart from a done step; a turn that ends after one says that the step could not be confirmed, and never that it was done
- [ ] **Tests**: a forced timeout after commit, a call that never left, the loop's messages, and the time limit itself
- [ ] **Real run**: the API and the server as two processes, the server stopped, written up under "Gate check"

## What the earlier phases already give

- `McpBackend.run` already catches every failure and turns it into an `error` result without a receipt. It never says "nothing was changed"
- The loop collects each tool's `receipt` and uses them in the two messages written from code, the model-failed-after-action message and the step-limit message. The model gets no turn to explain in either, so these are where an unknown step has to be said
- `calls_made` records `"name: status"` for each call and goes into the hand-over ticket, so a person who picks the ticket up already sees every call's status. An `unknown` status shows there with no change
- `create_server(..., tools=...)` takes the tool table, so a test can run a real app with a tool that commits and then stalls
- Refunds are bounded by units already refunded (`refunds.py` sums earlier refunds for the item), so asking again for a whole line is denied

## What the client knows

`McpBackend.run` has three stages: connect and initialize, send `tools/call`, wait for the result. What it can say depends on how far it got.

| Failed at | The server may have run the tool? | Status | What the model and customer are told |
| --- | --- | --- | --- |
| Connecting, initializing, or the server refusing the token | No: no `tools/call` was sent | `error` | The tool server could not be reached, and the step was not done |
| After `tools/call` was sent: the connection drops, the call times out, the answer cannot be read | Yes, possibly after committing | `unknown` | The result of this step is unknown. It may or may not have been done. Do not assume either |
| The result arrived | Not applicable | The server's status | As today |

The line between the first two rows is one flag, set immediately before `call_tool`. A failure with the flag unset happened before anything that changes data could run, because `initialize` has no side effects.

This also fixes the wording from phase 8. Today a server that is simply down says "may or may not have been done". After this phase it says it was not done, which is true.

## The timeout

`MCP_CALL_TIMEOUT_SECONDS` (default 30) bounds the whole call: connect, initialize, send and wait. The SDK's own defaults (30 seconds to connect, 5 minutes to read) are what could stall a turn today.

- A timeout that fires before the flag is set is a `not done` error. One that fires after it is `unknown`
- If the result has already arrived and only closing the connection is slow, the result is returned. A good answer is never thrown away because of a slow goodbye
- The loop's turn deadline is still checked only between steps, so a turn can run over it by up to one call timeout. The default is small enough that this is acceptable; making the call's timeout depend on the time the turn has left is a possible later change
- Timing out does not cancel the work on the server for sure. The server may finish and commit after the client has given up, which is exactly the case `unknown` is for

## Which steps matter

An unknown read is harmless: the model can say it could not look something up. An unknown write needs saying to the customer. `ToolSpec` gets an optional `action`, a short customer-facing name for what the tool changes, `None` for reads:

| Tool | `action` |
| --- | --- |
| `issue_refund` | `"your refund"` |
| `create_ticket` | `"your support ticket"` |
| `escalate` | `"the escalation to our support team"` |
| `lookup_order`, `list_orders`, `search_knowledge` | `None` |

For an unknown result of a tool with an `action`, the output's `receipt` is `Whether {action} went through could not be confirmed.` For one without, there is no receipt. The result's text, which the model reads, always carries the unknown message, and for a write it also tells the model not to repeat the call.

## The loop

Receipts stay "what was done". An unknown note is not that. The loop keeps them apart:

- A result with status `unknown` puts its receipt in a second list, `unconfirmed`, and not in `receipts`
- The model-failed message gains a branch. With receipts it says as before that part of the request was already done, then the receipts. With unconfirmed notes it adds that something could not be confirmed, then the notes. With both it says both
- The step-limit message does the same
- With only unconfirmed notes the message must not say that part of the request "was already done", because nothing is known to be done

The wording is fixed in code and in the tests below. The model-written answer is a separate matter: after a normal unknown result the model sees the text and tells the customer itself.

## Configuration

| Setting | Default | Used by |
| --- | --- | --- |
| `MCP_CALL_TIMEOUT_SECONDS` | `30` | `McpBackend`, as the time limit of one call |
| `TOOL_BACKEND` | `local` | Unchanged |

## Tests

| Test | Checks |
| --- | --- |
| Timeout after commit | A real app with a tool that commits a ticket and then stalls, and a short timeout: the output is `unknown`, the ticket exists, the text does not contain "nothing was changed", and the receipt says it could not be confirmed |
| A write that is unknown | `issue_refund`, `create_ticket` and `escalate` each give a receipt that names their `action` |
| A read that is unknown | A timed-out `list_orders` is `unknown` with no receipt |
| Never left | A server that is down, one that refuses the token, and a timeout while connecting each give `error`, say the step was not done and have no receipt |
| Result already in | A result that arrived followed by a slow close is returned, not turned into `unknown` |
| The limit | The call stops after about the configured time and not at the SDK's defaults |
| Loop, model fails after unknown | The saved message says it could not be confirmed and does not say it was done |
| Loop, done then unknown | A refund that succeeded and a ticket that is unknown give both in the message |
| Loop, step limit | The hand-over message and ticket carry the unknown step |
| Local unchanged | The existing suite passes unchanged and `LocalBackend` never returns `unknown` |

## Build order

1. `MCP_CALL_TIMEOUT_SECONDS` in the settings and `.env.example`, and its test
2. `ToolSpec.action`, and the table above in the registry
3. `McpBackend`: the "not left" flag, the `error` and `unknown` outcomes, the timeout; tests with a server that stalls after committing
4. The loop keeps `unconfirmed` apart from `receipts`; tests for the three messages
5. The real run with the server stopped, written into this file

## Gate check

To fill in when the phase is done.

## Decisions to confirm

- **Idempotency keys stay out.** `MCP_DESIGN.md` lists them as a later change. Without one, a repeated `issue_refund` after an unknown result could refund twice for a line with several units, because refunds are bounded by units and not by request. The defenses are the loop never retrying a tool, the unknown text telling the model not to repeat it, and the policy engine's unit check. A key is the real fix and is listed under "Not in this phase"
- **A flag, not exception types.** The SDK reports failures as exception groups whose members vary. A flag set just before `call_tool` does not depend on which exception arrived. The cost is that a failure during `initialize` is always "not done", which is true because `initialize` changes nothing
- **The unknown note as a `receipt`.** It reuses the field the loop already reads for its code-written messages, so the loop's change is small. The `unconfirmed` list keeps it from being counted as done. The alternative is a new field on `ToolOutput`, which would reach the MCP mapping and the tests for it
- **One timeout for the whole call**, not separate connect and read limits. Simpler, and one number to set. Phase 10's latency numbers show whether it needs splitting
- **Default 30 seconds.** The loop's turn deadline is 120. A turn can still overrun it by one call. Proposed: leave it and note it

## Not in this phase

- Idempotency keys for `issue_refund`
- A call timeout that depends on the time the turn has left
- Retrying a tool call, after any outcome
- The agent eval on both backends and the latency numbers (phase 10)
- Pooling or reusing connections
- Fetching the tool list from the server
- Moving the tool code out of the backend package
