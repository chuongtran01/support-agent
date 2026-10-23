import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RequireAuth } from "@/components/require-auth";
import LoginPage from "@/app/login/page";
import { AuthProvider } from "@/lib/auth";
import { getToken, setToken } from "@/lib/token";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/chat",
}));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const alex = { name: "Alex Rivera", role: "customer", membership: "basic" };
const taylor = { name: "Taylor Brooks", role: "approver", membership: "none" };

/** Answers /me with the given profile, and logins with a token for that profile. */
function backend(profile: typeof alex, password = "demo") {
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/me")) return reply(200, profile);
    if (url.endsWith("/auth/login")) {
      const body = JSON.parse(init!.body as string);
      return body.password === password
        ? reply(200, {
            token: "tok-1",
            token_type: "bearer",
            customer: profile,
          })
        : reply(401, { detail: "Wrong email or password" });
    }
    throw new Error(`unexpected ${url}`);
  });
}

const app = (children: ReactNode) =>
  render(<AuthProvider>{children}</AuthProvider>);

beforeEach(() => sessionStorage.clear());
afterEach(() => {
  fetchMock.mockReset();
  replace.mockReset();
});

describe("RequireAuth", () => {
  it("sends someone with no token to the login page", async () => {
    app(<RequireAuth>secret</RequireAuth>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the page to someone whose token the backend accepts", async () => {
    setToken("tok");
    backend(alex);
    app(<RequireAuth>secret</RequireAuth>);
    expect(await screen.findByText("secret")).toBeInTheDocument();
    expect(
      new Headers(fetchMock.mock.calls[0][1].headers).get("Authorization"),
    ).toBe("Bearer tok");
  });

  it("drops a token the backend refuses and goes to the login page", async () => {
    setToken("old");
    fetchMock.mockResolvedValue(reply(401, { detail: "Invalid token" }));
    app(<RequireAuth>secret</RequireAuth>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(getToken()).toBeNull();
  });

  it("keeps the token and says so when the backend cannot be reached", async () => {
    setToken("tok");
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    app(<RequireAuth>secret</RequireAuth>);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /can't reach the server/i,
    );
    expect(getToken()).toBe("tok");
    expect(replace).not.toHaveBeenCalled();
  });

  it("says not allowed to a customer on an approver page", async () => {
    setToken("tok");
    backend(alex);
    app(<RequireAuth role="approver">secret</RequireAuth>);
    expect(await screen.findByText("Not allowed")).toBeInTheDocument();
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });

  it("shows an approver page to an approver", async () => {
    setToken("tok");
    backend(taylor);
    app(<RequireAuth role="approver">secret</RequireAuth>);
    expect(await screen.findByText("secret")).toBeInTheDocument();
  });
});

describe("LoginPage", () => {
  it("signs in a demo customer with one click and opens the chat", async () => {
    backend(alex);
    app(<LoginPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Alex Rivera/ }),
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat"));
    expect(getToken()).toBe("tok-1");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      email: "alex@example.com",
      password: "demo",
    });
  });

  it("sends an approver to the approvals page", async () => {
    backend(taylor);
    app(<LoginPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Taylor Brooks/ }),
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/approvals"));
  });

  it("says so for a wrong password, and stays on the page", async () => {
    backend(alex, "right");
    app(<LoginPage />);
    await userEvent.type(
      await screen.findByLabelText("Email"),
      "alex@example.com",
    );
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Wrong email or password.",
    );
    expect(getToken()).toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("leaves the page for someone who is already signed in", async () => {
    setToken("tok");
    backend(alex);
    app(<LoginPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat"));
  });
});
