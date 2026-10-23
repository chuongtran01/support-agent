import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { setToken } from "@/lib/token";
import { UNREACHABLE, useLoad } from "./use-load";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const alex = { name: "Alex Rivera", role: "customer", membership: "basic" };
const wrapper = ({ children }: { children: ReactNode }) => (
  <AuthProvider>{children}</AuthProvider>
);

/** /me always works; /orders answers from the queue, then repeats its last answer. */
function serve(...answers: Array<Response | Error>) {
  let calls = 0;
  fetchMock.mockImplementation(async (url: string) => {
    if (url.endsWith("/me")) return json(200, alex);
    const answer = answers[Math.min(calls++, answers.length - 1)];
    if (answer instanceof Error) throw answer;
    return answer.clone();
  });
  return () => calls;
}

const orders = (...ids: number[]) =>
  json(
    200,
    ids.map((id) => ({ id })),
  );
const load = () =>
  renderHook(() => useLoad((api) => api.orders()), { wrapper });

beforeEach(() => {
  sessionStorage.clear();
  setToken("tok");
});
afterEach(() => fetchMock.mockReset());

describe("useLoad", () => {
  it("is loading, then has the data", async () => {
    serve(orders(1, 2));
    const { result } = load();
    expect(result.current).toMatchObject({
      data: null,
      error: null,
      loading: true,
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual([{ id: 1 }, { id: 2 }]);
    expect(result.current.error).toBeNull();
  });

  it("gives the backend's message when the call fails", async () => {
    serve(json(500, { detail: "Something went wrong." }));
    const { result } = load();
    await waitFor(() =>
      expect(result.current.error).toBe("Something went wrong."),
    );
    expect(result.current).toMatchObject({ data: null, loading: false });
  });

  it("says the server cannot be reached when nothing answers", async () => {
    serve(new TypeError("Failed to fetch"));
    const { result } = load();
    await waitFor(() => expect(result.current.error).toBe(UNREACHABLE));
  });

  it("fetches again on reload and clears the error", async () => {
    const calls = serve(json(500, { detail: "Nope." }), orders(7));
    const { result } = load();
    await waitFor(() => expect(result.current.error).toBe("Nope."));
    act(() => result.current.reload());
    expect(result.current.error).toBeNull();
    await waitFor(() => expect(result.current.data).toEqual([{ id: 7 }]));
    expect(calls()).toBe(2);
  });

  it("keeps the old data while a reload runs, and when it fails", async () => {
    serve(orders(1), json(500, { detail: "Nope." }));
    const { result } = load();
    await waitFor(() => expect(result.current.data).toEqual([{ id: 1 }]));
    act(() => result.current.reload());
    expect(result.current.data).toEqual([{ id: 1 }]); // not blanked while it runs
    await waitFor(() => expect(result.current.error).toBe("Nope."));
    expect(result.current.data).toEqual([{ id: 1 }]);
    expect(result.current.loading).toBe(false);
  });

  it("lets the page change the data it holds", async () => {
    serve(orders(1, 2, 3));
    const { result } = load();
    await waitFor(() => expect(result.current.data).toHaveLength(3));
    act(() => result.current.update((all) => all.filter((o) => o.id !== 2)));
    expect(result.current.data).toEqual([{ id: 1 }, { id: 3 }]);
  });

  it("does nothing when asked to change data that has not loaded", async () => {
    serve(orders(1));
    const { result } = load();
    act(() => result.current.update(() => []));
    expect(result.current.data).toBeNull();
    await waitFor(() => expect(result.current.data).toEqual([{ id: 1 }]));
  });

  it("ignores an older answer that arrives after a newer one", async () => {
    const pending: Array<(r: Response) => void> = [];
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith("/me")) return json(200, alex);
      return new Promise<Response>((resolve) => pending.push(resolve));
    });
    const { result } = load();
    await waitFor(() => expect(pending).toHaveLength(1));
    act(() => result.current.reload());
    await waitFor(() => expect(pending).toHaveLength(2));
    await act(async () => pending[1](orders(2))); // the newer call answers first
    await waitFor(() => expect(result.current.data).toEqual([{ id: 2 }]));
    await act(async () => pending[0](orders(1))); // the older one comes in late
    expect(result.current.data).toEqual([{ id: 2 }]);
  });
});
