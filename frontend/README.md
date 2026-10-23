# Frontend

Next.js (App Router, TypeScript, Tailwind, shadcn/ui) chat and refund-approval UI for the
support API. See `docs/PHASE5.md` for the plan.

```sh
npm install
cp .env.example .env.local   # NEXT_PUBLIC_API_URL, the backend's address
npm run dev                  # http://localhost:3000
npm run typecheck && npm run lint && npm test
npm run e2e   # browser tests; first time: npx playwright install chromium
```

From the repo root the same things are `make frontend-install`, `frontend-dev`,
`frontend-check` (type-check, lint and tests) and `frontend-build`.

The backend must allow this origin: `CORS_ORIGINS` in `.env` defaults to
`http://localhost:3000`.

Next.js here is a newer version than most documentation covers. `AGENTS.md` points to the
docs bundled in `node_modules/next/dist/docs/`; read those before writing framework code.
