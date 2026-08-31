"use client";

import { useState } from "react";
import Link from "next/link";
import type { ProviderCapability } from "./publication-server";

declare global {
  interface Window {
    Plaid?: {
      create(options: {
        token: string;
        onSuccess(publicToken: string): void;
        onExit(error: unknown): void;
      }): { open(): void; destroy(): void };
    };
  }
}

export default function ManageProviders({
  providers,
  csrfToken,
}: {
  providers: ProviderCapability[];
  csrfToken: string;
}) {
  const [state, setState] = useState<"idle" | "starting" | "linking" | "exchanging" | "done" | "error">("idle");
  const [message, setMessage] = useState("");
  const m1 = providers.find(provider => provider.provider === "M1 Finance");
  const busy = state === "starting" || state === "linking" || state === "exchanging";

  async function connectM1() {
    if (busy) return;
    setState("starting");
    setMessage("Creating a secure Plaid Link session…");
    try {
      const response = await ownerPost("/api/manage/providers/plaid/link-token", {}, csrfToken);
      const linkToken = typeof response.linkToken === "string" ? response.linkToken : null;
      if (!linkToken) throw new Error("Plaid Link did not return a token.");
      await loadPlaidLink();
      if (!window.Plaid) throw new Error("Plaid Link did not load.");
      setState("linking");
      setMessage("Complete the M1 connection in Plaid Link.");
      const handler = window.Plaid.create({
        token: linkToken,
        onSuccess(publicToken) {
          setState("exchanging");
          setMessage("Securing the read-only M1 connection…");
          void ownerPost(
            "/api/manage/providers/plaid/exchange",
            { publicToken },
            csrfToken,
          ).then(() => {
            handler.destroy();
            setState("done");
            setMessage("M1 is connected. Reloading provider status…");
            window.location.reload();
          }).catch(() => {
            handler.destroy();
            setState("error");
            setMessage("The M1 connection could not be completed. No publication was changed.");
          });
        },
        onExit() {
          handler.destroy();
          setState("idle");
          setMessage("Plaid Link was closed without changing the connection.");
        },
      });
      handler.open();
    } catch {
      setState("error");
      setMessage("Plaid Link is unavailable. No publication was changed.");
    }
  }

  return <section className="manage-source-grid">
    {providers.map(provider => <article className={`card ${provider.enabled ? "" : "muted-source"}`} key={provider.provider}>
      <span className="eyebrow">{provider.provider === "M1 Finance" ? "Plaid Investments" : "Broker connection"}</span>
      <h2>{provider.provider}</h2>
      <div className="provider-capabilities"><span>{provider.readOnly ? "Read-only" : "Unavailable"}</span><span>{provider.holdings ? "Holdings" : "No holdings"}</span><span>{provider.activities ? "Activities" : "No activities"}</span><span>{provider.accountCount} account{provider.accountCount === 1 ? "" : "s"}</span></div>
      <p>{provider.message ?? providerStatusMessage(provider)}</p>
      {provider.provider === "Webull" && <Link className="secondary" href="/?source=webull">Open Webull controls</Link>}
      {provider.provider === "M1 Finance" && <button className="primary" type="button" onClick={connectM1} disabled={busy || !provider.enabled}>{m1?.configured ? "Reconnect M1" : "Connect M1 with Plaid"}</button>}
      {provider.provider === "Charles Schwab" && <span className="pill">Disabled</span>}
    </article>)}
    {!providers.some(provider => provider.provider === "M1 Finance") && <article className="card muted-source"><span className="eyebrow">Plaid Investments</span><h2>M1 Finance</h2><p>The private service has not enabled the M1 provider.</p><button className="primary" type="button" disabled>Connect M1 with Plaid</button></article>}
    {message && <div className={`manage-provider-status ${state === "error" ? "error" : ""}`} role={state === "error" ? "alert" : "status"}>{message}</div>}
  </section>;
}

function providerStatusMessage(provider: ProviderCapability): string {
  if (!provider.enabled) return "This provider is disabled for the current release.";
  if (provider.status === "connected") return "The server-side read-only connection is ready.";
  if (provider.status === "action_required") return "Owner action is required before the next reconciled snapshot.";
  return "The connection is configured but currently unavailable.";
}

async function ownerPost(
  path: string,
  body: Record<string, unknown>,
  csrfToken: string,
): Promise<Record<string, unknown>> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
      "x-portfolio-csrf": csrfToken,
    },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new Error("Owner request failed.");
  }
  return payload as Record<string, unknown>;
}

let plaidScriptPromise: Promise<void> | null = null;

function loadPlaidLink(): Promise<void> {
  if (window.Plaid) return Promise.resolve();
  if (plaidScriptPromise) return plaidScriptPromise;
  plaidScriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Plaid Link failed to load."));
    document.head.appendChild(script);
  });
  return plaidScriptPromise;
}
