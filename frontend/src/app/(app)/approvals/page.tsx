"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import type { Decision, PendingRefund } from "@/api/types";
import { RequireAuth } from "@/components/require-auth";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuth } from "@/lib/auth";
import { formatCents } from "@/lib/money";

const REASONS: Record<string, string> = {
  changed_mind: "Changed mind",
  defect: "Defect",
  our_error: "Our error",
};

const when = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

interface Notice {
  id: number;
  tone: "default" | "destructive";
  text: string;
}

interface Confirming {
  refund: PendingRefund;
  decision: Decision;
}

function Approvals() {
  const { api } = useAuth();
  const [rows, setRows] = useState<PendingRefund[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [confirming, setConfirming] = useState<Confirming | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  const [loads, setLoads] = useState(0); // bumped to fetch the list again

  useEffect(() => {
    let current = true;
    api
      .pendingRefunds()
      .then((list) => current && setRows(list))
      .catch(
        (error) =>
          current &&
          setLoadError(
            error instanceof ApiError
              ? error.message
              : "Can't reach the server. Check that the backend is running.",
          ),
      );
    return () => {
      current = false;
    };
  }, [api, loads]);

  const reload = () => {
    setLoadError(null);
    setLoads((n) => n + 1);
  };

  const say = (tone: Notice["tone"], text: string) =>
    setNotices((all) => [...all, { id: Date.now() + all.length, tone, text }]);
  const remove = (id: number) =>
    setRows((all) => all?.filter((r) => r.refund_id !== id) ?? null);

  function ask(refund: PendingRefund, decision: Decision) {
    setConfirmError(null);
    setConfirming({ refund, decision });
  }

  async function confirm() {
    if (!confirming) return;
    const { refund, decision } = confirming;
    const what = `${formatCents(refund.amount_cents)} refund for ${refund.customer_name}`;
    setSubmitting(true);
    setConfirmError(null);
    try {
      await api.decide(refund.refund_id, decision);
      remove(refund.refund_id);
      say(
        "default",
        `${decision === "approve" ? "Approved" : "Rejected"} the ${what}.`,
      );
      setConfirming(null);
    } catch (error) {
      if (
        error instanceof ApiError &&
        (error.status === 404 || error.status === 409)
      ) {
        // Someone else decided it first, or it is gone: it is not pending, so it leaves the list.
        remove(refund.refund_id);
        say("destructive", `The ${what} was not decided. ${error.message}`);
        setConfirming(null);
      } else if (error instanceof ApiError && error.status === 403) {
        say("destructive", `The ${what} was not decided. ${error.message}`);
        setConfirming(null);
      } else {
        setConfirmError(
          error instanceof ApiError
            ? error.message
            : "Can't reach the server. Try again.",
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Approvals</h1>
          <p className="text-sm text-muted-foreground">
            Refunds waiting for a decision, oldest first.
          </p>
        </div>
        <Button variant="outline" onClick={reload}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {notices.map((notice) => (
        <Alert key={notice.id} variant={notice.tone}>
          <AlertDescription>{notice.text}</AlertDescription>
        </Alert>
      ))}

      {loadError && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center gap-3">
            <span>{loadError}</span>
            <Button size="sm" variant="outline" onClick={reload}>
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {rows === null && !loadError && (
        <div
          className="flex flex-col gap-2"
          role="status"
          aria-label="Loading refunds"
        >
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      )}

      {rows?.length === 0 && (
        <p className="py-10 text-center text-muted-foreground">
          No refunds are waiting for approval.
        </p>
      )}

      {rows && rows.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Customer</TableHead>
              <TableHead>Item</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead>Requested</TableHead>
              <TableHead>
                <span className="sr-only">Decision</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((refund) => (
              <TableRow key={refund.refund_id}>
                <TableCell className="font-medium">
                  {refund.customer_name}
                </TableCell>
                <TableCell>
                  {refund.item_name} × {refund.quantity}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatCents(refund.amount_cents)}
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">
                    {REASONS[refund.reason] ?? refund.reason}
                  </Badge>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {when.format(new Date(refund.created_at))}
                </TableCell>
                <TableCell>
                  <div className="flex flex-col items-end gap-1">
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        disabled={refund.is_own}
                        aria-label={`Approve refund for ${refund.customer_name}`}
                        onClick={() => ask(refund, "approve")}
                      >
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={refund.is_own}
                        aria-label={`Reject refund for ${refund.customer_name}`}
                        onClick={() => ask(refund, "reject")}
                      >
                        Reject
                      </Button>
                    </div>
                    {refund.is_own && (
                      <span className="text-xs text-muted-foreground">
                        Your own refund. Another approver must decide it.
                      </span>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <AlertDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open && !submitting) setConfirming(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirming?.decision === "approve"
                ? "Approve this refund?"
                : "Reject this refund?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirming &&
                `${confirming.refund.customer_name}: ${confirming.refund.item_name} × ${confirming.refund.quantity}, ${formatCents(confirming.refund.amount_cents)}. A decision cannot be undone.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {confirmError && (
            <p role="alert" className="text-sm text-destructive">
              {confirmError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={submitting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant={
                confirming?.decision === "reject" ? "destructive" : "default"
              }
              disabled={submitting}
              onClick={() => void confirm()}
            >
              {confirming?.decision === "approve"
                ? "Approve refund"
                : "Reject refund"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default function ApprovalsPage() {
  return (
    <RequireAuth role="approver">
      <Approvals />
    </RequireAuth>
  );
}
