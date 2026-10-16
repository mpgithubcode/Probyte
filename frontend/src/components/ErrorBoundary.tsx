import { Component, type ReactNode } from "react";

const RELOADED = "probyte-reloaded-for-update";

/** A part of the page fails to load after an update (its file is from the old version): load the new version, once. */
function isStaleChunk(e: unknown): boolean {
  const m = String((e as Error)?.message ?? e);
  return /dynamically imported module|Importing a module script failed|Loading chunk|ChunkLoadError|error loading dynamically/i.test(m);
}

/**
 * Something in one part of the page failing (a value it didn't expect, a bug) shows a message there with a way to try
 * again, instead of the whole app going blank; the sidebar and everything else keep working. `resetKey`: shows the part
 * again when it changes (another table opened).
 */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string; label?: string; compact?: boolean },
  { error: Error | null; key?: string }> {
  state: { error: Error | null; key?: string } = { error: null, key: this.props.resetKey };

  static getDerivedStateFromError(error: Error) { return { error }; }

  static getDerivedStateFromProps(props: { resetKey?: string }, state: { error: Error | null; key?: string }) {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null;
  }

  componentDidCatch(error: Error) {
    if (isStaleChunk(error)) {
      let again = false;
      try { again = !!sessionStorage.getItem(RELOADED); sessionStorage.setItem(RELOADED, "1"); } catch { /* storage off */ }
      if (!again) { location.reload(); return; }
    }
    console.error(error);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const stale = isStaleChunk(error);
    return (
      <div className={`crash ${this.props.compact ? "compact" : ""}`} role="alert">
        <h2>{stale ? "Probyte was updated" : `${this.props.label ?? "This part"} couldn't be shown`}</h2>
        <p>{stale ? "Reload to use the new version." : "Something in it didn't load as expected. Your data is safe."}</p>
        {!stale && <details><summary>Details</summary><pre>{error.message}</pre></details>}
        <div className="crash-actions">
          {!stale && <button className="btn" onClick={() => this.setState({ error: null })}>Try again</button>}
          <button className="btn primary" onClick={() => location.reload()}>Reload</button>
        </div>
      </div>
    );
  }
}

// a page that loaded fine after the reload: an update later can reload once again
try { window.addEventListener("load", () => setTimeout(() => sessionStorage.removeItem(RELOADED), 10000)); } catch { /* storage off */ }
