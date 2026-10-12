"use client";

// Header link to the loan screening module. Shows only when the API has the module switched on
// (PIQ_SCREENING_ENABLED), so turning the module off also removes the link. Delete this file and
// its one line in Shell.tsx to remove the module from the UI entirely.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

export default function ScreeningNavLink() {
  const [on, setOn] = useState(false);
  const path = usePathname();

  useEffect(() => {
    let token: string | null = null;
    try { token = window.sessionStorage.getItem("piq_token"); } catch { /* ignore */ }
    fetch("/api/screening/status", { headers: token ? { Authorization: `Bearer ${token}` } : {}, cache: "no-store" })
      .then((r) => setOn(r.ok))
      .catch(() => setOn(false));
  }, []);

  if (!on) return null;
  const active = path.startsWith("/screening");
  return (
    <nav style={{ display: "flex", gap: 14, fontSize: 13 }} aria-label="Modules">
      <Link href="/" style={{ fontWeight: active ? 400 : 650, color: active ? undefined : "var(--text)" }}>Portfolio</Link>
      <Link href="/screening" style={{ fontWeight: active ? 650 : 400, color: active ? "var(--text)" : undefined }}>Loan screening</Link>
    </nav>
  );
}
