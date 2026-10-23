# Phase 5: Frontend

**Status:** done
**Gate:** full demo flow works

## Goal

Put a web UI in front of the API from phase 4. By the end, a customer can log in, chat with the agent and watch the answer stream in with its sources, and an approver can log in and decide pending refunds. The demo flow (a cited answer, a refund made, a refund held for approval, the approver deciding it) runs end to end in a browser against the real backend and model.

The backend is not expected to change. If the UI turns out to need an endpoint, it is added here with a test, not worked around in the browser. One was: `GET /orders` (see the orders page).

## Deliverables

- [x] **App scaffold**: `frontend/`, Next.js 16 (App Router) with TypeScript, Tailwind 4 and shadcn/ui, Vitest with Testing Library, `npm` scripts for dev, build, lint, type-check and tests, and `make` targets for them (`make frontend-install`, `frontend-dev`, `frontend-build`, `frontend-check`)
- [x] **API client and types**: `frontend/src/api/`. A typed client for every endpoint, with the request and response types generated from the backend's OpenAPI spec (`make api-types`) so they cannot drift, and hand-written types for the stream events (OpenAPI does not describe them)
- [x] **Login and session**: `/login` with a one-click button per seeded demo customer and the approver, the token kept for the tab, a header with the customer's name and membership, log out, and a redirect to `/login` on any `401`
- [x] **Stream reader**: `fetch` with the `Authorization` header and a small server-sent-events parser, because `EventSource` cannot send headers. Pure functions, unit tested
- [x] **Chat page**: `/chat`. Session list, message thread and composer, the answer streaming in, a line showing which tool is running, the sources under each answer, and clear states for every error the API can return
- [x] **Orders page**: `/orders`, for every signed-in person. The customer's own orders newest first, with status, dates, total, items, and the status and amount of each refund, so they know what to ask. "Ask about this order" and "Ask about a return" open the chat with a question in the box (not sent). Added after step 6 with one backend endpoint, `GET /orders`
- [x] **Approvals page**: `/approvals`, approvers only. The pending refunds with customer, item, quantity, amount and reason, approve and reject buttons, and your own refund shown but not decidable
- [x] **Tests**: unit tests for the parser and the chat state, and browser tests for the flows with the API mocked
- [x] **Full-stack demo run**: the demo script below, run in a browser against the real backend and model, with the result recorded in this file

## Architecture

```
Browser (Next.js, client components)
   |  fetch + Authorization: Bearer <token>      (CORS: CORS_ORIGINS in the backend)
   v
FastAPI (phase 4, unchanged)
```

The browser calls the backend directly at `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). There is no Next.js server logic: every screen is per-customer data behind a token, so the pages are client components and the Next.js server only serves them.

| Route | Who | What |
| --- | --- | --- |
| `/login` | anyone | Pick a demo customer or type an email and the seed password |
| `/chat` | customers | The chat. A new chat is made on the first message; the open chat is kept in the address as `?session=<id>`, so a reload comes back to it |
| `/orders` | anyone signed in | The caller's own orders, items and refunds, with buttons that open the chat with a question in the box (`/chat?ask=...`, which the chat page reads once and removes from the address) |
| `/approvals` | approvers | The pending refunds. A customer who opens it sees a "not allowed" page, and the API refuses the calls in any case |

The sidebar shows New chat, an Approvals link when the role is `approver`, the chats grouped by date, and at the bottom an avatar with the name and membership from `GET /me` and a menu with Log out. The role in the UI is only for what to show; the backend decides what is allowed on every call.

### The chat state

One reducer holds a chat: the list of messages, whether a turn is running, and the live answer. The stream reader feeds it events (`text`, `tool`, `done`, `error`), so the screen is a function of that state and the reducer is tested without a browser.

**The chat view is a small component of our own.** A `Thread` component renders the messages from the reducer and keeps the list scrolled to the bottom while an answer streams, unless the reader has scrolled up; a `Composer` takes the text, sends on Enter, adds a new line on Shift+Enter and is disabled while a turn runs. Both are built from shadcn parts and are about a couple of hundred lines in all. The tool status line and the source chips are ordinary components in the thread. A chat library was considered and left out: this chat has no edit, regenerate, branch or attachment features, so most of such a library would be switched off and the reducer would still need an adapter to feed it.

- **Sending.** The user's message appears at once. The composer is disabled while a turn runs, which matches the backend's one turn at a time; a `409 turn_in_progress` (a second tab, say) shows "Still answering the last message".
- **Streaming.** `text` events append to the live answer, and `tool` events set a status line such as "Looking up your order..." (names mapped to plain words; the API does not send arguments). The `done` event saves the message id and the sources.
- **Sources.** The prompt makes the model write `[document_id]` after a fact. When the answer is finished, a marker for a document in the `done` event's citations becomes a numbered chip that opens the source's title and heading, and a marker for anything else is removed (the backend dropped it and counted it). While the answer is streaming the raw text is shown.
- **Text, not markup.** Answers are plain text with line breaks, rendered by React's escaping, so the UI never renders HTML from the model.
- **Reloading.** Opening a session loads `GET /sessions/{id}/messages`, which already has the citations, so a reloaded chat looks like the live one.
- **Errors.** `error` events and failed requests show a message in the thread and leave the composer usable. `404` on a session sends the customer back to a new chat, `409 chat_full` offers "Start a new chat", and `422` (too long) is stopped in the composer at 2,000 characters.
- **Leaving mid-answer.** The backend finishes and saves the turn even if the browser goes away, so the next visit shows the whole answer.

### The approvals page

A table of pending refunds from `GET /approvals`, oldest first, with the amount in dollars. Approve and reject ask for confirmation in a dialog first, because a decision cannot be undone; confirming calls `POST /approvals/{id}/decision`, removes the row on success and shows the result. `409` (someone else decided it) and `404` remove the row with a note, `403` for your own refund is prevented in the UI (`is_own` disables the buttons and says why) and still handled if it comes back. An empty list says so.

### Look and feel

Plain, readable and responsive down to a phone width. The components come from shadcn/ui, which is built on headless primitives (Base UI in the current default style) and Tailwind and is copied into `frontend/src/components/ui/` by its CLI, so they are our own files. The ones this phase uses:

| Part of the UI | shadcn component |
| --- | --- |
| Login form, buttons | `Button`, `Input`, `Label`, `Card` |
| The chat thread and composer | our own `Thread` and `Composer`, built from `Message`, `Bubble`, `Marker` (the "looking up your order" line), `MessageScroller` (follows the streaming answer and offers a scroll-to-end button), `Textarea` and `Button` |
| Navigation, the chat list and the account menu; a drawer on a phone | `Sidebar` (icon-rail collapse), `Avatar`, `DropdownMenu`, `Tooltip` |
| A source chip that shows the document title and heading | `Popover` (or `HoverCard`) with `Badge` |
| The pending refunds | `Table`, `Badge`, `Skeleton` while loading, `Alert` for the notes |
| Confirming approve or reject | `AlertDialog` |
| Errors and "answering..." notes | `Alert`, `Sonner` toasts |

The primitives give the keyboard and screen-reader behavior of the dialogs, popovers and drawers. The thread is a live region so a screen reader announces the answer, the composer submits on Enter and takes a new line on Shift+Enter, and focus returns to the composer after a turn.

## Tests

None needs the backend, an API key or the network.

- **Unit (Vitest):** the SSE parser (frames split across reads, several frames in one read, a frame with unusual whitespace, the stream ending early), the chat reducer (a full turn, a tool-only reply, an error mid-answer, `done` with citations), and the citation marker replacement (valid, invalid, repeated, no markers).
- **Browser (Playwright, API mocked with route interception; `make frontend-e2e`, desktop and phone widths, plus an axe-core accessibility check):** log in as a customer and as an approver; send a message and see it stream and the sources appear; each error state; a customer cannot see the Approvals link and sees "not allowed" at `/approvals`; approve and reject (after the confirmation) remove the row; a `401` goes to the login page.
- **Types:** `tsc --noEmit` and lint run with the tests, and a check fails if the generated API types differ from what the backend's spec produces.

The model is not in these tests. Whether the agent answers well is measured by the phase 4 evals; this phase checks that the UI shows what the API sends.

## Demo script (the gate)

Run in a browser against the real backend, the seeded database and the real model:

1. Log in as Alex (Basic). Ask "What's the minimum order for free shipping?" The answer says $75 with a source, not the stale $50.
2. Ask to return the boots from the 5-day-old order. The refund is made, the answer states the amount after the label fee, and nothing is held.
3. Log out. Log in as Priya (Plus). Ask to return the tent from the 45-day-old order. The answer says the refund is waiting for approval.
4. Log out. Log in as the approver. The refund is on the Approvals page with Priya's name, the tent and $421.05 (the $429.00 price less the $7.95 label fee). Approve it.
5. Log in as Priya again. Ask about the tent order. The agent reports the refund as approved.
6. As Alex, ask about Priya's order. The agent says it could not find it on his account.

## Build order

- [x] 1. Scaffold the app, the `make` targets and the checks (type-check, lint, tests). Notes: the installed Next.js (16.4) is newer than most documentation, so framework code is written against the docs in `node_modules/next/dist/docs/` (`AGENTS.md` says so); the scaffold turns on Cache Components and Partial Prefetching, which are turned off here because there is nothing to prerender and hidden routes staying mounted would interact with a streaming answer; Vitest needed `vite` and `@testing-library/dom` installed explicitly; `npm audit` reports a high-severity `braces` advisory reached only through the lint config and the shadcn CLI, with no fix that does not downgrade them.
- [x] 2. The API client, the generated types and the stream parser, with the unit tests. Notes: `make api-types` dumps `app.openapi()` and runs `openapi-typescript` into `frontend/src/api/schema.d.ts` (committed); `make api-types-check` regenerates it and fails on a diff. The client is `createApi({baseUrl, getToken, onUnauthorized})` (later split by area: the shared plumbing and `ApiError` in `http.ts`, and `auth.ts`, `chat.ts`, `orders.ts` and `approvals.ts` each returning its own methods, composed in `client.ts`, which the pages still import), so the token store (step 3) plugs in without the client knowing where the token lives. `sendMessage` is an async generator of typed events; a 401 calls `onUnauthorized` only when a token was sent, so a wrong password on the login form does not look like an expired session. FastAPI's three `detail` shapes (string, `{code, message}`, a 422 list) become one `ApiError`.
- [x] 3. Login, the token, the header and the route guards. Notes: `AuthProvider` (`src/lib/auth.tsx`) restores the session by calling `GET /me` with the stored token and holds one of four states (loading, anonymous, unreachable, signed in); `RequireAuth` redirects an anonymous visitor to `/login`, shows "Not allowed" for the wrong role, and keeps the token and offers a retry when the backend cannot be reached (so a restart is not a logout); the routes under `src/app/(app)/` share the guard and header, and `/` redirects to `/chat`. The demo password (`demo`) is a constant in the login page. Checked by curl against the real backend: the CORS preflight and a login work from `http://localhost:3000`; not yet run in a browser.
- [x] 4. The chat reducer and the chat page (thread, composer, sources), with the unit and browser tests. Notes: the state is one reducer (`src/chat/reducer.ts`) and `useChat` plays the stream into it; the open chat is a single page with the session in `?session=` (written with `history.replaceState`, read once on load) instead of a `/chat/[id]` route, because changing the route when the first message creates the session would remount the page in the middle of the answer. The words written before a tool call are dropped from the live answer, because the backend does not keep them, so a reload shows what was streamed. A request that fails before any event gives the message back to the box, including a network failure. The "browser tests" are Testing Library tests of the whole page in jsdom with `fetch` mocked (10 of them, over the page, composer, errors and sources); Playwright is not set up yet and is left for step 6 or 7. The session list is hidden below the `md` width; the phone drawer is step 6. Checked by curl that the real backend's frames (`tool` with `started`/`finished`, then `text`) match the parser; the page itself has not been run in a browser.
- [x] 5. The approvals page and its tests. Notes: a table of the pending refunds with an `AlertDialog` that asks before a decision is sent; a decision removes the row and leaves a note, a `404` or `409` removes the row with the backend's message (the refund is no longer pending), a `403` keeps the row and says why, and any other failure stays in the dialog so the approver can try again. Added a Refresh button, since new refunds do not push to the page. Tested with Testing Library over a mocked `fetch` (11 tests, over the list, both decisions, cancel, own refund, each error and the empty and failed list). Checked by curl against the real backend that `GET /approvals` and the `404` for an unknown refund have the shapes the page reads; the page has not been run in a browser.
- [x] 6. Error and edge states, responsive layout and the accessibility pass. Notes: Playwright is now set up (`make frontend-e2e`; 29 tests, each run at a desktop and a phone width, against a production build on port 3100 with its own `.next-e2e` folder and the backend mocked in the browser with `page.route`), and axe-core runs inside it (WCAG 2.x A/AA, with colour contrast, which jsdom cannot check). Screenshots at both widths found three real problems that the jsdom tests could not: on a phone the approvals table ran off-screen so Approve and Reject could not be reached (the rows are now cards below `md`, from one DOM); there was no way to switch chats on a phone (a drawer opened by a Chats button); and the source chip was the same colour as its bubble (now bordered, and 24px for the touch target). Axe found `--muted-foreground` under 4.5:1 on `--muted`, so it is darkened in `globals.css`. Also added: a loading state while a saved chat opens, a not-found page, an error boundary, a page title per route, `aria-current` on the active link, a table label, and `motion-reduce` on the spinner. `typecheck` now runs `next typegen` first, because the route types it needs are generated, and a fresh clone has none. **Sidebar layout (after step 6, at the owner's request):** the top header and the phone Chats drawer are replaced by one shadcn `Sidebar` on every page, laid out like a chat product: New chat and Approvals at the top, the chats under Today, Yesterday, Previous 7 days and Older, and the avatar with a Log out menu at the bottom. It collapses to an icon rail on a desktop (Cmd/Ctrl+B) and is a slide-in drawer on a phone, which gets a slim top bar holding only the menu button. The open chat is now the address again: the chats are links to `/chat?session=<id>` and the chat page follows `useSearchParams`, so the sidebar, Back and a reload all open the right chat; a chat made by the first message is written into the address with `history.replaceState`. This replaces the "read once, then follow the state" approach of step 4. The chat list lives in a `SessionsProvider` shared by the sidebar and the page. shadcn's `use-mobile` hook is changed to `useSyncExternalStore`, because the original set state in an effect and failed the lint rule. Not done: a dark theme (the shadcn dark tokens exist but nothing switches to them), and screen-reader testing by hand.
- [x] 6b. The orders page (after step 6, at the owner's request). Notes: the customer could not tell what to ask, and the agent cannot list orders either (`lookup_order` needs an order number and nothing lists them), so a customer who did not know a number could not start a return. `GET /orders` returns the same `OrderView` the tool returns, newest first, from a new `app/orders.py` that both the endpoint and `lookup_order` now call, so the scoping to the caller's own orders is in one place (8 API tests, including that no customer sees another's orders; the 402 existing tests still pass). The page offers "Ask about a return" only for a delivered item with units that no refund has used and that is not final sale (policy still decides; this only hides the pointless), and "Ask about this order" on every order; both link to `/chat?ask=<question>`, which fills the composer, focuses it and leaves sending to the person. The buttons are real links (`buttonVariants` on a `Link`), not Base UI buttons with a `render` prop, which would have been announced as buttons. Their hidden text starts with the visible text, which axe requires. Pages reach the client with `useApi()` (which is `useAuth().api`, named for what the page wants), and the three places that fetch a list when the page opens (orders, approvals and the sidebar's chats) share `useLoad((api) => api.orders())` in `src/lib/use-load.ts`: it returns `data`, `error`, `loading`, `reload` and `update`, keeps the old data while a reload runs or fails, and drops an older answer that arrives after a newer one (8 tests). **What to ask (added after the orders page):** a new visitor still did not know what to say. An empty chat now offers clickable starter questions (`Suggestions`): two general policy questions, plus up to two about the person's own orders with real order numbers in them (a return for the newest returnable item, and a where-is-it for an order that has not arrived), built by the pure `suggestionsFor` from the same `GET /orders` data, so it works without the agent being able to list orders. Picking one fills the box and focuses it; it is not sent. The login page shows what each demo account is set up to show (for example "Return the tent: over $200, so it waits for approval"), taken from `backend/app/seed.py` and the demo script. Not done: a `list_orders` tool for the agent, which changes its behaviour and needs the evals re-run (suggested next, as its own change).
- [x] 7. Run the demo script against the real stack, fix what it finds, and record the run. See "Demo run" below.

## Demo run

Run on 2026-10-22 in headless Chromium, with the real backend, the freshly seeded dev database, the real knowledge base (51 documents) and the real model (`gpt-6-luna`, `medium` reasoning). It is a script, `frontend/e2e-real/demo.spec.ts` (`make frontend-demo`), not a click-through by hand, so it can be repeated; it drives the same screens a person would (the demo buttons, the Orders page, the composer, the confirmation dialog) and then checks the result **in the backend**, not only on the screen. It uses its own ports (backend :8001, frontend :3101) so it does not disturb a running backend or `next dev`.

**Result: all six steps passed, in two runs** (re-seeded between them; about 25 and 32 seconds). The wording of an answer differs from run to run, the outcomes did not.

| Step | What the agent said (second run) | In the backend |
| --- | --- | --- |
| 1. Alex: minimum order for free shipping | "Standard shipping is free on orders of $75 or more." with a source chip. No stale $50 | |
| 2. Alex: return the boots (from Orders, "Ask about a return") | "Your return is approved, and the $121.05 refund has been made after the $7.95 return label fee." | refund `completed`, 12105 cents |
| 3. Priya: return the tent (from Orders) | "...pending review because refunds over $200 require approval. It has not been issued yet." | refund `pending`, 42105 cents |
| 4. Taylor (approver): Approvals page | Priya Shah, Four-season tent × 1, $421.05 (and the seeded $240.00 parka); approved after the dialog; "Approved the $421.05 refund for Priya Shah." | refund `approved` |
| 5. Priya: status of the tent refund | "The refund for the four-season tent on order #10 is approved. One unit was refunded for $421.05." The Orders page also showed "Approved $421.05" | |
| 6. Alex: where is Priya's order #10 | "I couldn't find order #10 on your account." Nothing about the tent or its price | |

The login-page hints for the other accounts were checked too (steps 7 to 9 of the script): Jordan's return went to manual review for having reached 3 refunds in 90 days and no refund was made; Morgan's return was made ($71.05, so the 91-day-old refund does not count); Sam's tent was refused because the order has not been delivered, and no refund exists.

**What it found.** Nothing in the app needed fixing. The first attempt failed at the login, because a backend was already running on :8000 with only `localhost:3000` allowed as an origin (the browser blocked the call), so the demo now uses its own backend and frontend ports. Two things worth knowing: the script's "log out, log in as" is a fresh browser context per person (the log-out menu itself is covered by the mocked browser tests), and the run changes the data it runs against, so `make seed` after it.

**What this does not show.** The errors are exercised in the mocked browser tests, not provoked against the real backend (a real `409 turn_in_progress` or a dropped stream is hard to cause on purpose). It was one model, two runs, so it says the flow works, not how often a model slips; that is what the phase 4 evals measure.

## Gate check

- [x] **Demo flow**: the six steps above work in a browser against the real backend and model
- [x] **Roles**: a customer cannot reach the approvals page or see its link, and an approver's decisions go through the API
- [x] **Errors**: every error the API returns has a visible, recoverable state
- [x] **Tests**: unit and browser tests pass with no API key and no backend
- [x] **Types**: the generated API types match the backend's spec (`make api-types-check`; not yet run in CI)

## Decisions to confirm

- **The token is kept in `sessionStorage`.** It survives a refresh and goes away with the tab. The alternative, an httpOnly cookie set through a Next.js route handler, keeps the token out of reach of page scripts but adds a server layer and a second place to enforce auth. Here the UI renders no HTML from the model and loads no third-party scripts, which is what makes the simpler choice acceptable. Either way the backend still checks the role on every call.
- **The browser calls FastAPI directly.** It needs the CORS setting that phase 4 already has, and it keeps the streaming path one hop shorter than proxying through Next.js.
- **shadcn/ui on Tailwind.** It gives accessible dialogs, popovers and tables without a runtime component library to upgrade: the components are copied in and are ours to edit. The cost is that we own that code, so only the components in the table above are added, and they are not covered by tests of their own.
- **Types generated from OpenAPI.** It removes a class of drift bugs and is cheap; the stream events are the exception and are written by hand.
- **Browser tests mock the API, and the gate is a manual run.** The real model's answers vary from run to run, so a test that depends on them would be flaky. The mocked tests are deterministic and the manual run proves the whole stack.
- **Plain-text answers, no Markdown.** The prompt already asks for short paragraphs without tables or headings, and plain text means nothing from the model is rendered as markup.
- **`npm` as the package manager**, since it ships with Node and the project needs nothing more.

## Not in this phase

- Registration, password reset, token refresh and "remember me" (the demo customers come from the seed)
- Markdown or rich rendering of answers, file uploads and voice
- Showing tool arguments or the model's reasoning (the API does not send them; Langfuse traces in phase 6 are the place for that)
- Telling a customer in the page when their refund is decided (they see it the next time they ask the agent)
- Deploying the frontend, a production Dockerfile and a hosted demo (phase 6, with the README and demo)
- Eval or trace dashboards in the UI
