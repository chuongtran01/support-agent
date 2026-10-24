import { defineConfig, devices } from "@playwright/test";

const PORT = 3101;
const BACKEND = process.env.DEMO_API_URL ?? "http://localhost:8001";

// The demo script, run in a real browser against the REAL backend, database and model (not
// the mocks of the normal suite). It uses its own ports so it does not disturb a backend or
// `next dev` that is already running. Start a backend for it, allowing its origin:
//   CORS_ORIGINS=http://localhost:3101 uvicorn app.main:app --port 8001
// and re-seed (`make seed`) before and after: the run decides a refund. `make frontend-demo`.
export default defineConfig({
  testDir: "./e2e-real",
  workers: 1, // the steps depend on each other
  retries: 0,
  timeout: 180_000,
  reporter: "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    ...devices["Desktop Chrome"],
    // DEMO_VIDEO=1 records each step (one clip per person) to test-results/; `make demo-video` joins them
    video: process.env.DEMO_VIDEO ? { mode: "on", size: { width: 1280, height: 720 } } : "off",
  },
  webServer: {
    command: `NEXT_DIST_DIR=.next-demo NEXT_PUBLIC_API_URL=${BACKEND} npm run build && NEXT_DIST_DIR=.next-demo npx next start -p ${PORT}`,
    url: `http://localhost:${PORT}/login`,
    timeout: 180_000,
    reuseExistingServer: true,
  },
});
