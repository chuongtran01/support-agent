"use client";

import { ClipboardCheck, LogOut, SquarePen } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuth } from "@/lib/auth";
import { groupSessions } from "@/lib/group-sessions";
import { useSessions } from "@/lib/sessions";

const MEMBERSHIPS: Record<string, string> = {
  basic: "Basic member",
  plus: "Plus member",
};

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("");
}

/** The chats, newest group first. Reads the open chat from the address, so it needs Suspense. */
function RecentChats() {
  const { sessions } = useSessions();
  const open = useSearchParams().get("session");
  const { isMobile, setOpenMobile } = useSidebar();
  const groups = useMemo(() => groupSessions(sessions), [sessions]);
  return groups.map((group) => (
    <SidebarGroup
      key={group.label}
      className="group-data-[collapsible=icon]:hidden"
    >
      <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {group.sessions.map((session) => (
            <SidebarMenuItem key={session.id}>
              <SidebarMenuButton
                isActive={session.id === open}
                render={<Link href={`/chat?session=${session.id}`} />}
                onClick={() => isMobile && setOpenMobile(false)}
              >
                <span>{session.preview ?? "Untitled chat"}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  ));
}

export function AppSidebar() {
  const { state, logout } = useAuth();
  const pathname = usePathname();
  const { isMobile, setOpenMobile } = useSidebar();
  if (state.status !== "signedIn") return null;
  const { name, role, membership } = state.profile;
  const closeOnPhone = () => isMobile && setOpenMobile(false);

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate px-2 font-semibold group-data-[collapsible=icon]:hidden">
            Northwind Outfitters
          </span>
          <SidebarTrigger />
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip="New chat"
                  render={<Link href="/chat" />}
                  onClick={closeOnPhone}
                >
                  <SquarePen />
                  <span>New chat</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              {role === "approver" && (
                <SidebarMenuItem>
                  <SidebarMenuButton
                    tooltip="Approvals"
                    isActive={pathname === "/approvals"}
                    render={<Link href="/approvals" />}
                    onClick={closeOnPhone}
                  >
                    <ClipboardCheck />
                    <span>Approvals</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <Suspense fallback={null}>
          <RecentChats />
        </Suspense>
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger render={<SidebarMenuButton size="lg" />}>
                <Avatar className="size-8">
                  <AvatarFallback>{initials(name)}</AvatarFallback>
                </Avatar>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-medium">{name}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {role === "approver"
                      ? "Approver"
                      : (MEMBERSHIPS[membership] ?? "No membership")}
                  </span>
                </div>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                side="top"
                align="start"
                className="min-w-48"
              >
                <DropdownMenuItem onClick={logout}>
                  <LogOut /> Log out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
