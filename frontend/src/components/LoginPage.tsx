import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import type { User } from "../types";

export function LoginPage({ onLogin }: { onLogin: (u: User) => void }) {
  const [config, setConfig] = useState<{ allow_signup: boolean; has_users: boolean } | null>(null);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get("/auth/config").then((c) => {
      setConfig(c);
      if (!c.has_users) setMode("register");
    }).catch((e) => setError(e.message));
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (mode === "register" && password !== confirm) return setError("Passwords don't match");
    setBusy(true);
    try {
      onLogin(await api.post<User>(`/auth/${mode}`, { username, password }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const register = mode === "register";
  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <div className="brand"><span className="logo">P</span><span>Probyte</span></div>
        <h2>{register ? (config?.has_users === false ? "Create the first account" : "Create an account") : "Sign in"}</h2>
        <label className="stack">Username
          <input className="input" autoFocus autoComplete="username" value={username}
            onChange={(e) => setUsername(e.target.value.toLowerCase())} />
          {register && <span className="hint">Lowercase letters, digits, “.”, “_” or “-”. Also your CalDAV login.</span>}
        </label>
        <label className="stack">Password
          <input className="input" type="password" autoComplete={register ? "new-password" : "current-password"} value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        {register && (
          <label className="stack">Confirm password
            <input className="input" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </label>
        )}
        {error && <div className="error">{error}</div>}
        <button className="btn primary block" disabled={busy || !username || !password}>
          {busy ? "…" : register ? "Create account" : "Sign in"}
        </button>
        {config?.allow_signup && config.has_users && (
          <button type="button" className="ghost-btn center" onClick={() => { setMode(register ? "login" : "register"); setError(""); }}>
            {register ? "Already have an account? Sign in" : "New here? Create an account"}
          </button>
        )}
      </form>
    </div>
  );
}
