import { Header } from "@/components/header";
import { RequireAuth } from "@/components/require-auth";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <RequireAuth>
      <Header />
      <main className="flex-1">{children}</main>
    </RequireAuth>
  );
}
