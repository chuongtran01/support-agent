import { RequireAuth } from "@/components/require-auth";

export default function ApprovalsPage() {
  return (
    <RequireAuth role="approver">
      <p className="p-6 text-muted-foreground">The pending refunds go here.</p>
    </RequireAuth>
  );
}
