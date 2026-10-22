import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createApi } from "./client";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

function api(token: string | null = "tok") {
  const onUnauthorized = vi.fn();
  return { client: createApi({ baseUrl: "http://api", getToken: () => token, onUnauthorized }), onUnauthorized };
}

const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => fetchMock.mockReset());

describe("createApi", () => {
  it("sends the token and parses JSON", async () => {
    fetchMock.mockResolvedValue(reply(200, { name: "Alex", role: "customer", membership: "basic" }));
    const { client } = api();
    expect(await client.me()).toMatchObject({ name: "Alex" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api/me");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("posts JSON for a decision", async () => {
    fetchMock.mockResolvedValue(reply(200, { status: "approved", refund_id: 3 }));
    await api().client.decide(3, "approve");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api/approvals/3/decision");
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"decision":"approve"}');
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  });

  it("calls onUnauthorized on a 401 when it had a token", async () => {
    fetchMock.mockResolvedValue(reply(401, { detail: "Invalid token" }));
    const { client, onUnauthorized } = api();
    await expect(client.me()).rejects.toMatchObject({ status: 401, message: "Invalid token" });
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("does not call onUnauthorized for a wrong password", async () => {
    fetchMock.mockResolvedValue(reply(401, { detail: "Wrong email or password" }));
    const { client, onUnauthorized } = api(null);
    await expect(client.login("a@b.c", "x")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("reads the code of a 409", async () => {
    fetchMock.mockResolvedValue(
      reply(409, { detail: { code: "turn_in_progress", message: "Wait for the answer." } }),
    );
    const events = api().client.sendMessage("s1", "hi");
    await expect(events.next()).rejects.toMatchObject({
      status: 409,
      code: "turn_in_progress",
      message: "Wait for the answer.",
    });
  });

  it("reads the first problem of a 422", async () => {
    fetchMock.mockResolvedValue(reply(422, { detail: [{ msg: "String should have at most 2000 characters" }] }));
    await expect(api().client.sendMessage("s1", "x").next()).rejects.toMatchObject({
      status: 422,
      code: "invalid",
      message: "String should have at most 2000 characters",
    });
  });

  it("streams the events of a message", async () => {
    const sse =
      'event: text\ndata: {"text":"Hi"}\n\n' +
      'event: done\ndata: {"message_id":2,"citations":[],"dropped_citations":0}\n\n';
    fetchMock.mockResolvedValue(new Response(sse, { status: 200 }));
    const seen = [];
    for await (const event of api().client.sendMessage("s1", "hello")) seen.push(event.type);
    expect(seen).toEqual(["text", "done"]);
    expect(fetchMock.mock.calls[0][1].body).toBe('{"text":"hello"}');
  });
});
