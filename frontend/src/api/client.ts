import { approvalsApi } from "./approvals";
import { authApi } from "./auth";
import { chatApi } from "./chat";
import { createHttp, type ApiOptions } from "./http";
import { ordersApi } from "./orders";

// The pages import the client and its error from here; the groups live in their own files.
export { ApiError, type ApiOptions } from "./http";

/** Every call the app makes to the backend, grouped by area in `auth.ts`, `chat.ts`,
 *  `orders.ts` and `approvals.ts`, over the shared plumbing in `http.ts`. */
export function createApi(options: ApiOptions) {
  const http = createHttp(options);
  return {
    ...authApi(http),
    ...chatApi(http),
    ...ordersApi(http),
    ...approvalsApi(http),
  };
}

export type Api = ReturnType<typeof createApi>;
