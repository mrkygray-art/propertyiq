"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getUser, signOut, type Health, type LockdownMode, type User } from "@/lib/api";
import ScreeningNavLink from "./ScreeningNavLink";

function Indicator({ ok, label }: { ok: boolean | null; label: string }) {
  const cls = ok === null ? "unknown" : ok ? "ok" : "bad";
  const state = ok === null ? "checking" : ok ? "ready" : "unavailable";
  return (
    <span title={`${label}: ${state}`}>
      <span className={`dot ${cls}`} aria-hidden="true" />
      {label} <span className="muted">{state}</span>
    </span>
  );
}

export default function Shell({ children }: { children: (user: User) => React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [lockdown, setLockdown] = useState<LockdownMode>("off");

  useEffect(() => {
    const u = getUser();
    if (!u) {
      router.replace("/login");
      return;
    }
    setUser(u);
    const check = () => {
      api.health().then(setHealth).catch(() => setHealth({ status: "degraded", database: false, local_model: false }));
      api.lockdownBanner().then((b) => setLockdown(b.mode)).catch(() => {});
    };
    check();
    const t = setInterval(check, 30_000);
    return () => clearInterval(t);
  }, [router]);

  if (!user) return null;

  return (
    <>
      <header className="topbar">
        <Link href="/" className="brand">Property<span>IQ</span></Link>
        <ScreeningNavLink />
        <div className="status" aria-label="System status">
          <Indicator ok={health ? health.database : null} label="Database" />
          <Indicator ok={health ? health.local_model : null} label="Local model" />
        </div>
        <div className="spacer" />
        <div className="user">
          {user.role === "admin" && <Link href="/admin">Admin</Link>}
          <span>{user.display_name}</span>
          <button className="linkbtn" onClick={() => { signOut(); router.replace("/login"); }}>Sign out</button>
        </div>
      </header>
      {lockdown !== "off" && (
        <div className="lockdown-banner" role="status">
          Offline lockdown is on: {lockdown === "computer" ? "this whole computer is" : "PropertyIQ's programs are"} blocked from the internet.
        </div>
      )}
      <main>{children(user)}</main>
    </>
  );
}
