"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type Api } from "@/api/client";
import { useApi } from "@/lib/auth";

interface Loaded<T> {
  /** Null until the first answer. After that it is kept while a reload runs or fails. */
  data: T | null;
  /** The message for the last attempt if it failed, otherwise null. */
  error: string | null;
  /** Nothing has come back yet, good or bad. */
  loading: boolean;
  reload: () => void;
  /** Change the data held here, for a row the page just removed. Does nothing before it loads. */
  update: (change: (data: T) => T) => void;
}

export const UNREACHABLE =
  "Can't reach the server. Check that the backend is running.";

/**
 * Fetches something when the page opens and again on `reload`. It ignores an answer that
 * arrives after the page has gone, and keeps showing the old data while a reload runs.
 * Write the call inline, `useLoad((api) => api.orders())`; the latest one is used.
 */
export function useLoad<T>(load: (api: Api) => Promise<T>): Loaded<T> {
  const api = useApi();
  const latest = useRef(load);
  useEffect(() => {
    latest.current = load;
  });
  const [state, setState] = useState<{ data: T | null; error: string | null }>({
    data: null,
    error: null,
  });
  const [loads, setLoads] = useState(0);

  useEffect(() => {
    let current = true;
    latest
      .current(api)
      .then((data) => current && setState({ data, error: null }))
      .catch(
        (error) =>
          current &&
          setState((before) => ({
            ...before,
            error: error instanceof ApiError ? error.message : UNREACHABLE,
          })),
      );
    return () => {
      current = false;
    };
  }, [api, loads]);

  const reload = useCallback(() => {
    setState((before) => ({ ...before, error: null }));
    setLoads((n) => n + 1);
  }, []);

  const update = useCallback((change: (data: T) => T) => {
    setState((before) =>
      before.data === null ? before : { ...before, data: change(before.data) },
    );
  }, []);

  return {
    data: state.data,
    error: state.error,
    loading: state.data === null && state.error === null,
    reload,
    update,
  };
}
