// The token lives in sessionStorage: it survives a refresh and goes away with the tab.
// Storage can throw (blocked site data, private windows), so every access is guarded.
const KEY = "support-agent.token";

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    // The user stays signed in until the page is reloaded.
  }
}

export function clearToken(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // Nothing to clear.
  }
}
