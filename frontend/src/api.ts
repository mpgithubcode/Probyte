/** An error from the server or the network, with the HTTP status (0: no answer at all). */
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

const RETRY_STATUS = new Set([502, 503, 504]);         // the server restarting or busy: worth asking again
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** What a 4xx/5xx answer says, readably: FastAPI's {"detail": "…"} or its list of field problems. */
function detailOf(j: any, fallback: string): string {
  const d = j?.detail ?? j;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d.map((x) => (x && typeof x === "object" ? [Array.isArray(x.loc) ? x.loc.filter((p: unknown) => p !== "body").join(".") : "", x.msg]
      .filter(Boolean).join(": ") : String(x))).join("; ") || fallback;
  }
  return d ? JSON.stringify(d) : fallback;
}

// Saves still on their way (and ones waiting a moment to be sent): something about to read back what was saved (a
// template made from a view, an export) first lets them all land, so it gets what's on the screen.
const inflight = new Set<Promise<unknown>>();
const flushers = new Set<() => void>();
/** Count a write made outside `api` (a keepalive fetch) as on its way. */
export function track<T>(p: Promise<T>): Promise<T> {
  inflight.add(p);
  p.finally(() => inflight.delete(p)).catch(() => undefined);
  return p;
}
/** A delayed save that can be sent at once when something needs it to have landed. */
export function onFlush(fn: () => void): () => void { flushers.add(fn); return () => { flushers.delete(fn); }; }
/** Send delayed saves now and wait for every save on its way. */
export async function settle(): Promise<void> {
  flushers.forEach((f) => f());
  await Promise.allSettled([...inflight]);
}

async function request<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  if (method !== "GET") return track(send<T>(method, path, body));
  return send<T>(method, path, body);
}

async function send<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  // Reads are asked again (a few times, waiting a little longer each time) when the network drops or the server is
  // restarting; writes never are (they may have happened), they report the problem instead.
  const reads = method === "GET";
  for (let attempt = 0; ; attempt++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), reads ? 120_000 : 300_000);
    let res: Response;
    try {
      res = await fetch(`/api${path}`, {
        method, signal: ctl.signal,
        headers: body !== undefined ? { "content-type": "application/json" } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      clearTimeout(timer);
      if (reads && attempt < 3) { await sleep(600 * 2 ** attempt); continue; }
      throw new ApiError(ctl.signal.aborted ? "Probyte took too long to answer. Try again."
        : navigator.onLine === false ? "You're offline: the change wasn't saved. Try again when you're back online."
        : "Can't reach Probyte right now (the network or the server). Try again in a moment.", 0);
    }
    clearTimeout(timer);
    if (reads && RETRY_STATUS.has(res.status) && attempt < 3) { await sleep(800 * 2 ** attempt); continue; }
    if (res.status === 401 && !path.startsWith("/auth/")) {
      // Session expired or logged out elsewhere: App listens for this and shows the login screen.
      window.dispatchEvent(new Event("probyte:unauthorized"));
    }
    if (!res.ok) {
      let msg = res.statusText || `Error ${res.status}`;
      try { msg = detailOf(await res.json(), msg); } catch { /* not json */ }
      throw new ApiError(msg, res.status);
    }
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }
}

export const api = {
  get: <T = any>(p: string) => request<T>("GET", p),
  post: <T = any>(p: string, b: unknown = {}) => request<T>("POST", p, b),
  patch: <T = any>(p: string, b: unknown) => request<T>("PATCH", p, b),
  put: <T = any>(p: string, b: unknown) => request<T>("PUT", p, b),
  del: <T = any>(p: string) => request<T>("DELETE", p),
  /** Send files (multipart form); the error message comes from the server's answer. */
  upload: async <T = any>(p: string, body: FormData, method = "POST"): Promise<T> => {
    let res: Response;
    try { res = await fetch(`/api${p}`, { method, body }); } catch {
      throw new ApiError("Can't reach Probyte right now: the upload didn't go through. Try again in a moment.", 0);
    }
    if (!res.ok) throw new ApiError(detailOf(await res.json().catch(() => ({ detail: res.statusText })), res.statusText), res.status);
    return res.json();
  },
};
