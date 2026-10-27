# Phase 10: The evals and the cost of the hop

**Status:** in progress (steps 1 to 5 of 7 done)
**Gate:** the agent eval gives the same pass rates on `local` and `mcp` within run-to-run noise, with no tool call lost to the transport; the added time per tool call is measured; the README has the numbers and the commands that reproduce them

This is phase 4 of `MCP_DESIGN.md`. Phases 7 to 9 built the server, the client and the failure handling, and tested them with scripted models. This phase asks the question the whole change exists to answer: does the real agent, with the real model, do as well through MCP as it does in-process, and what does the hop cost?

**Do phase 11 first.** It adds an idempotency key and one retry to `McpBackend`. The numbers here should be for the client as it will stay, so this phase runs after `docs/PHASE11.md` is done. The file numbers follow the original plan, not the order of work.

## Goal

Run the dev split three times and the test split once on each backend, compare, and publish. The evals are the specification: they already exist, so nothing new is written to judge the result. The only new code is a `--backend` flag, a timer around tool calls, and a check that the server is up before an MCP run starts.

By the end the README says, with numbers: the agent passes as often through MCP as without it, and each tool call costs this many extra seconds at the median and the 95th percentile.

## Deliverables

- [x] **`--backend`**: `agent_eval.py` takes `--backend local|mcp` (default `local`) and builds the agent's tools accordingly (`build_backend` returns `None` for `local`, `McpBackend(settings.mcp_server_url)` for `mcp`, and refuses any other name)
- [x] **Preflight**: an `mcp` run first checks that the server answers, and stops with a clear message if it does not. It seeds once so a customer exists, lists the tools through `McpBackend.list_tools`, and compares names and input schemas with the registry's
- [x] **Tool timing**: every tool call's duration is recorded on both backends, and the report gives the median and 95th percentile per backend. `TimedBackend` wraps the backend (a `LocalBackend` for `local`); `tool_timing` gives calls, all/reads/writes spreads in milliseconds, and `lost` (`error`, `unknown` or raised), printed in the report and kept in the summary under `tools`
- [x] **Result files**: the file name and its contents say which backend produced them, and traces carry the backend as a tag. The file is `agent-<split>-<backend>-<stamp>.json` (earlier files have no backend in the name), it holds `backend` at the top and in the summary, the traces' `eval_run` tag is the same name, and `local` or `mcp` is added to their tags
- [ ] **Tests**: the new pieces, next to the harness's existing tests in `test_agent_eval.py`
- [ ] **Recorded runs**: dev three times and test once, on each backend, with the result files described under "Gate check"
- [ ] **Comparison**: the two backends side by side, case by case, with every difference explained
- [ ] **README**: a section with the numbers, and rows in "Reproduce the numbers"
- [ ] **`MCP_DESIGN.md`**: the roadmap and risk table brought up to date

## What the earlier work already gives

- `agent_eval.py` builds one `AgentDeps` in `main`, and the loop already takes `AgentDeps.tools`. Selecting a backend is one argument there
- The report already has pass rates by category and trap, tokens, cost, and seconds per message (median and 95th percentile, whole turn and to the first word). It needs only a backend label and the per-tool timing
- `settings.mcp_server_url` is where `McpBackend` finds the server, and `make mcp` starts it on the same database, so the eval needs no new wiring to reach it
- `--repeat` and the flaky-case report already exist. The earlier phases measured the noise on this exact configuration: the same dev run gave 124 of 129 and later 122 of 129 on the 43 older cases with nothing changed
- Result files and traces are already tagged by split, case and run

## The backend flag

`--backend local` is today's behaviour and builds no backend, so `AgentDeps.tools` stays `None`. `--backend mcp` builds `McpBackend(settings.mcp_server_url)`. Everything else in the harness is the same for both, so any difference between the two result files is the transport.

The eval **resets the business tables** of the database in `DATABASE_URL`, as it always has. The MCP server shares that database, so an MCP run needs `make mcp` running against the same one. The server reads the customers and orders the eval just seeded, which is why the server holds no copy of the data.

## The preflight

An MCP run with the server down would not fail loudly: every tool call would come back as an `error` ("could not be reached, step not done") and the model would apologise, so the run would produce a plausible-looking low score. The harness therefore connects before the first case, lists the tools with a token for a seeded customer, and stops with `the MCP server at <url> is not answering; start it with make mcp` if that fails. It also stops if the tool list differs from the registry's, which would mean the server is running older code.

## The timer

Latency has to be measured the same way on both backends, so the timer wraps the backend and not the transport. A small `TimedBackend` holds any `ToolBackend`, runs each call through it, and records the tool name, the status and the seconds. For `local` it wraps a `LocalBackend` built from the same sessions and embedder the loop would have built, so the wrapper adds no work of its own beyond reading the clock.

The report then gives, per backend: tool calls made, median and 95th percentile seconds per call, and the same split by read and write tools. Subtracting the `local` row from the `mcp` row is the cost of the hop, and the same figure appears in the per-message numbers, where the model's time dominates.

The report also counts tool calls whose status is `error` or `unknown`. On a healthy run that count is zero on both backends, and it is part of the gate: a non-zero count on `mcp` means the transport lost calls and the pass rate is not comparable.

## What is compared

| Check | How it is scored |
| --- | --- |
| Overall pass rate, dev | Three runs per backend, as in phase 6: 177 runs each |
| Overall pass rate, test | One run per backend, 36 cases |
| Refund decided correctly | Must be the same on both; the policy engine is the same code |
| Right tools called, injection resisted, off-topic declined | Per category, both backends |
| Transport losses | Tool calls ending in `error` or `unknown`: zero on both |
| Flaky cases | Which cases differ between runs, per backend |
| Cost and latency | Per tool call and per message, median and 95th percentile |

**What counts as the same.** The model answers differently each time, and the earlier phases saw the dev total move by two to three cases with nothing changed. The two backends match if their dev totals differ by no more than that spread, and no case that passes every run on `local` fails every run on `mcp`. The second condition catches a real, systematic difference that a small total would hide. Every case that differs in either direction is named in the comparison with its cause or "noise". The test split is one run, so a case or two is noise there and is read, not tuned on.

**If the backends differ.** That is a finding, not a failure of the phase. The likely causes are a different error text for malformed arguments (the model sees it only when it sent malformed JSON), a timeout that cut a slow call, or a difference in what the tool schemas say. Each is checked in the case's trace before it is called noise.

## Cost of the runs

The README puts a dev run of three repeats at about ten cents. Two backends and a test run each is about four times that, so a few tens of cents of model calls. `make seed` after each, as the README already says.

## Tests

| Test | Checks |
| --- | --- |
| Flag | `local` builds no backend, `mcp` builds an `McpBackend` with the configured URL, anything else is refused |
| Preflight | A server that is not there stops the run with the message and before any case; a server whose tool list differs from the registry's stops it too |
| `TimedBackend` | Returns exactly what the wrapped backend returns, records one entry per call with its name, status and a duration, and records a failing call too |
| Report | The per-backend timing rows, the transport-loss count, and the backend label are in the summary and the file |
| File name | The backend is in the result file's name and contents; the traces carry it as a tag |
| Existing harness tests | Pass unchanged, with `local` the default |

## Build order

1. ~~`--backend` in the harness, `McpBackend` built from the setting, and its test~~ (done)
2. ~~The preflight and its tests~~ (done; run for real against a stopped and a running server)
3. ~~`TimedBackend`, the timing in the report, the transport-loss count, and their tests~~ (done)
4. ~~The backend in the result file name, contents and trace tags~~ (done; the file contents and tags are checked by the real run in step 5, the name by a test)
5. ~~A short real run of each backend on a few cases (`--only`) to check the plumbing before spending on the full runs~~ (done, see "Plumbing check")
6. The full recorded runs: dev three times and test once, `local` then `mcp`, `make seed` between and after
7. The comparison, the README section and the commands, and `MCP_DESIGN.md`

## Plumbing check

Run 2026-10-27, six dev cases on each backend (`a001` search, `a031` lookup, `a032` refund, `a051` ticket, `a052` escalate, `a058` list orders), one run each, no judge. `make seed` afterwards.

| | `local` | `mcp` |
| --- | --- | --- |
| Passed | 6 of 6 | 6 of 6 |
| Tool calls | 8, 0 lost | 8, 0 lost |
| Tool call, median / p95 | 7 ms / 714 ms | 31 ms / 354 ms |
| Reads / writes, median | 7 ms / 6 ms | 30 ms / 34 ms |

The preflight passed against the real server, which logged 26 `POST /mcp` requests and no errors. Both result files carry `backend` at the top and in the summary. Six cases are too few to compare: the 714 ms and 354 ms p95 are single `search_knowledge` calls, which wait on the embedding API. Traces were not checked, because this machine has no Langfuse keys, so the tag change is only covered by reading the code.

A first attempt played no cases: in zsh an unquoted `$IDS` is one argument, so `--only` matched nothing. The report printed `0/0` and a result file with no cases; that file was deleted. `--only` with an id that matches no case should probably be an error (not done here).

## Gate check

To fill in when the phase is done.

## Decisions to confirm

- **The server for MCP runs is the real process (`make mcp`)**, not an in-process app. That is the deployment being measured, so the latency includes a real socket and a real second process. The cost is one more thing to start before an MCP run, which the preflight turns into a clear message
- **Time the backend, not the transport.** The same wrapper on both backends gives one comparable number. The cost is that "latency of the hop" includes the server's work (the database and the tool), not only the network, which is also what the agent experiences
- **The noise rule.** Totals within the spread seen before, and no case that always passes on one and always fails on the other. Proposed because a total alone can hide a real difference, and a strict case-by-case match would flag noise
- **Run local again, not reuse the old baseline.** The loop and `McpBackend` changed since the recorded 2026-10-24 numbers. A fresh `local` run is also the regression check for phases 8 to 11. Proposed: rerun, and keep the old numbers in the README's history if they differ
- **Test split once per backend**, not tuned on, as in earlier phases

## Not in this phase

- Pooling or reusing connections, whatever the latency numbers say. A connection per turn is the next step if the cost matters, and is its own piece of work
- Splitting the single call timeout into connect and read limits (the numbers inform it; the change is separate)
- Idempotency (phase 11)
- Fetching the tool list from the server
- Moving the tool code out of the backend package
- Anything about Gmail, Calendar or memory: this project ends at the README numbers
