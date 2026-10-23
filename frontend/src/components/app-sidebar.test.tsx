import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppSidebar } from "@/components/app-sidebar";
import { SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/lib/auth";
import { SessionsProvider } from "@/lib/sessions";
import { getToken, setToken } from "@/lib/token";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const alex = { name: "Alex Rivera", role: "customer", membership: "basic" };
const taylor = { name: "Taylor Brooks", role: "approver", membership: "none" };

const now = new Date();
const hoursAgo = (h: number) =>
  new Date(now.getTime() - h * 3_600_000).toISOString();
const SESSIONS = [
  { id: "s-new", created_at: now.toISOString(), preview: "Where is my order?" },
  { id: "s-old", created_at: hoursAgo(24 * 30), preview: "Return the boots" },
  { id: "s-blank", created_at: hoursAgo(24 * 20), preview: null },
];

function open(profile: typeof alex, sessions = SESSIONS) {
  setToken("tok");
  fetchMock.mockImplementation(async (url: string) => {
    if (url.endsWith("/me")) return json(200, profile);
    if (url.endsWith("/sessions")) return json(200, sessions);
    throw new Error(`unexpected ${url}`);
  });
  return render(
    <AuthProvider>
      <SessionsProvider>
        <TooltipProvider>
          <SidebarProvider>
            <AppSidebar />
          </SidebarProvider>
        </TooltipProvider>
      </SessionsProvider>
    </AuthProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, "", "/chat");
});
afterEach(() => {
  fetchMock.mockReset();
  replace.mockReset();
});

describe("AppSidebar", () => {
  it("shows the person at the bottom, with their membership, and no Approvals link to a customer", async () => {
    open(alex);
    expect(await screen.findByText("Alex Rivera")).toBeInTheDocument();
    expect(screen.getByText("Basic member")).toBeInTheDocument();
    expect(screen.getByText("AR")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute(
      "href",
      "/chat",
    );
    expect(
      screen.queryByRole("link", { name: "Approvals" }),
    ).not.toBeInTheDocument();
  });

  it("shows the Approvals link, and the word Approver, for an approver", async () => {
    open(taylor);
    expect(
      await screen.findByRole("link", { name: "Approvals" }),
    ).toHaveAttribute("href", "/approvals");
    expect(screen.getByText("Approver")).toBeInTheDocument();
  });

  it("lists the chats under date headings, each a link that opens it", async () => {
    open(alex);
    const today = await screen.findByText("Today");
    expect(today).toBeInTheDocument();
    expect(screen.getByText("Older")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Where is my order?" }),
    ).toHaveAttribute("href", "/chat?session=s-new");
    expect(
      screen.getByRole("link", { name: "Return the boots" }),
    ).toHaveAttribute("href", "/chat?session=s-old");
    expect(
      screen.getByRole("link", { name: "New chat", description: "" }),
    ).toBeInTheDocument();
  });

  it("marks the open chat", async () => {
    window.history.replaceState(null, "", "/chat?session=s-old");
    open(alex);
    const link = await screen.findByRole("link", { name: "Return the boots" });
    expect(link).toHaveAttribute("data-active");
    expect(
      screen.getByRole("link", { name: "Where is my order?" }),
    ).not.toHaveAttribute("data-active");
  });

  it("logs out from the menu on the avatar", async () => {
    open(alex);
    await userEvent.click(
      await screen.findByRole("button", { name: /Alex Rivera/ }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "Log out" }),
    );
    expect(getToken()).toBeNull();
    await waitFor(() =>
      expect(screen.queryByText("Alex Rivera")).not.toBeInTheDocument(),
    );
  });
});
