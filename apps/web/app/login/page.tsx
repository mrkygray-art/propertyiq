"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, login } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("analyst");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Invalid username or password." : "Sign-in is unavailable. Is the API running?");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="card login-card">
        <div className="brand" style={{ fontSize: 22 }}>Property<span>IQ</span></div>
        <p className="sub" style={{ marginTop: 4 }}>Private analytics on a synthetic real estate portfolio. Everything runs on this computer.</p>
        <form onSubmit={submit}>
          <label>
            Demo user
            <select value={username} onChange={(e) => setUsername(e.target.value)}>
              <option value="analyst">analyst (all funds, 55 properties)</option>
              <option value="restricted">restricted (Synthetic Fund II only)</option>
              <option value="guest">guest (demo viewer)</option>
              <option value="admin">admin (security checklist, lockdown)</option>
            </select>
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </label>
          {error && <div className="error" role="alert">{error}</div>}
          <button className="btn" type="submit" disabled={busy || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
        <p className="hint">The demo password is <code>PIQ_DEMO_PASSWORD</code> in the project&apos;s <code>.env</code> file.</p>
      </div>
    </div>
  );
}
