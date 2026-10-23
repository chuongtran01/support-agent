import type { NextConfig } from "next";

// Every screen is per-customer data behind a token, fetched in the browser, so there is
// nothing to prerender or cache on the server. Cache Components (and the Partial Prefetching
// that goes with it) is left off to keep navigation plain: with it on, Next.js keeps hidden
// routes mounted, which would interact with a streaming answer.
const nextConfig: NextConfig = {
  // The browser tests build into their own folder, so they do not disturb a running `next dev`.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  turbopack: {
    // A package-lock.json in the home directory confuses root detection; pin it here
    root: import.meta.dirname,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
