import type { Http } from "./http";
import type { Decision, DecisionResult, PendingRefund } from "./types";

export const approvalsApi = ({ json, post }: Http) => ({
  pendingRefunds: () => json<PendingRefund[]>("/approvals"),
  decide: (refundId: number, decision: Decision) =>
    json<DecisionResult>(`/approvals/${refundId}/decision`, post({ decision })),
});
