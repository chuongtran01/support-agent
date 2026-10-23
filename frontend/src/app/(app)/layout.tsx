import { AppSidebar } from "@/components/app-sidebar";
import { RequireAuth } from "@/components/require-auth";
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SessionsProvider } from "@/lib/sessions";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <SessionsProvider>
        <TooltipProvider>
          <SidebarProvider className="h-svh">
            <AppSidebar />
            <SidebarInset className="min-h-0 min-w-0 overflow-hidden">
              {/* The sidebar is a drawer on a phone, so it needs a button to open it. */}
              <div className="flex h-12 shrink-0 items-center gap-2 border-b px-2 md:hidden">
                <SidebarTrigger />
                <span className="font-semibold">Northwind Outfitters</span>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
            </SidebarInset>
          </SidebarProvider>
        </TooltipProvider>
      </SessionsProvider>
    </RequireAuth>
  );
}
