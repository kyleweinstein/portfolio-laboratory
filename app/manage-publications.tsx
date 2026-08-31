"use client";

import { useState } from "react";
import type { ManagedPublication, OwnerProviderAccount } from "./publication-server";

export default function ManagePublications({
  initialPublications,
  accounts,
  csrfToken,
}: {
  initialPublications: ManagedPublication[];
  accounts: OwnerProviderAccount[];
  csrfToken: string;
}) {
  const [publications, setPublications] = useState(initialPublications);
  const [pending, setPending] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    accountHandle: accounts[0]?.accountHandle ?? "",
    title: "",
    slug: "",
    benchmarkSymbol: "SPY",
    enabled: true,
  });

  function editPublication(item?: ManagedPublication) {
    setForm(item ? {
      accountHandle: item.accountHandle ?? accounts[0]?.accountHandle ?? "",
      title: item.title,
      slug: item.slug,
      benchmarkSymbol: item.benchmarkSymbol ?? "SPY",
      enabled: item.published,
    } : {
      accountHandle: accounts[0]?.accountHandle ?? "",
      title: "",
      slug: "",
      benchmarkSymbol: "SPY",
      enabled: true,
    });
    setEditing(true);
    setMessage("");
  }

  async function saveConfiguration() {
    if (pending || !form.accountHandle) return;
    setPending("configure");
    setMessage("");
    try {
      const response = await fetch("/api/manage/publications/configure", {
        method: "PUT",
        credentials: "same-origin",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
          "x-portfolio-csrf": csrfToken,
        },
        body: JSON.stringify(form),
      });
      if (!response.ok) throw new Error("configuration failed");
      setEditing(false);
      setMessage("Portfolio card settings were saved. Reloading the reconciled owner view…");
      window.location.reload();
    } catch {
      setMessage("Portfolio card settings could not be saved. The existing follower revision is unchanged.");
      setPending(null);
    }
  }

  async function changePublication(item: ManagedPublication) {
    if (!item.publicationId || pending) return;
    const nextPublished = !item.published;
    setPending(item.publicationId);
    setMessage("");
    try {
      const response = await fetch(
        `/api/manage/publications/${encodeURIComponent(item.publicationId)}`,
        {
          method: nextPublished ? "POST" : "DELETE",
          credentials: "same-origin",
          headers: {
            accept: "application/json",
            "content-type": "application/json",
            "x-portfolio-csrf": csrfToken,
          },
          body: nextPublished ? "{}" : undefined,
        },
      );
      if (!response.ok) throw new Error("publication failed");
      setPublications(current => current.map(publication =>
        publication.publicationId === item.publicationId
          ? { ...publication, published: nextPublished }
          : publication,
      ));
      setMessage(nextPublished
        ? `${item.title} is published to Discord members.`
        : `${item.title} is withheld. Its follower route is no longer available.`);
    } catch {
      setMessage(`The ${nextPublished ? "publish" : "unpublish"} action failed. The previous follower revision is unchanged.`);
    } finally {
      setPending(null);
    }
  }

  return <>
    <div className="manage-publication-heading"><button className="secondary" type="button" disabled={!accounts.length || Boolean(pending)} onClick={() => editPublication()}>Configure portfolio card</button>{!accounts.length && <span className="note">Connect and sync a read-only brokerage account before configuring a card.</span>}</div>
    {editing && <div className="manage-config-form"><label>Brokerage account<select value={form.accountHandle} onChange={event => setForm(current => ({ ...current, accountHandle: event.target.value }))}>{accounts.map(account => <option value={account.accountHandle} key={account.accountHandle}>{account.provider} · {account.accountType ?? "Investment account"}{account.currency ? ` · ${account.currency}` : ""}</option>)}</select></label><label>Card title<input value={form.title} maxLength={120} onChange={event => setForm(current => ({ ...current, title: event.target.value }))}/></label><label>URL slug<input value={form.slug} maxLength={64} pattern="[a-z0-9-]+" onChange={event => setForm(current => ({ ...current, slug: event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") }))}/></label><label>Benchmark<input value={form.benchmarkSymbol} maxLength={30} onChange={event => setForm(current => ({ ...current, benchmarkSymbol: event.target.value.toUpperCase() }))}/></label><label className="manage-check"><input type="checkbox" checked={form.enabled} onChange={event => setForm(current => ({ ...current, enabled: event.target.checked }))}/> Eligible to publish after reconciliation</label><div className="manage-row-actions"><button className="primary" type="button" onClick={saveConfiguration} disabled={pending === "configure" || !form.accountHandle || !form.title.trim() || !form.slug}>{pending === "configure" ? "Saving…" : "Save settings"}</button><button className="secondary" type="button" onClick={() => setEditing(false)} disabled={pending === "configure"}>Cancel</button></div></div>}
    {publications.length ? <div className="table-wrap"><table><thead><tr><th>Portfolio</th><th>Provider</th><th>Status</th><th>Quality</th><th>Performance through</th><th>Issues</th><th>Controls</th></tr></thead><tbody>{publications.map(item => <tr key={item.slug}><td><strong>{item.title}</strong><small>/{item.slug}</small></td><td>{item.provider}</td><td>{item.published ? "Published" : "Withheld"}</td><td>{item.quality.replaceAll("_", " ")}</td><td>{item.performanceThrough ?? "Unavailable"}</td><td>{item.issueCount}</td><td><div className="manage-row-actions"><button className="secondary compact" type="button" disabled={Boolean(pending)} onClick={() => editPublication(item)}>Edit</button><button className={item.published ? "secondary compact" : "primary compact"} type="button" disabled={!item.publicationId || Boolean(pending)} onClick={() => changePublication(item)}>{pending === item.publicationId ? "Working…" : item.published ? "Unpublish" : "Publish"}</button>{item.publicationId ? <a className="secondary compact" href={`/manage/preview/${item.publicationId}?slug=${encodeURIComponent(item.slug)}`}>Privacy preview</a> : <button className="secondary compact" disabled>Privacy preview</button>}</div></td></tr>)}</tbody></table></div> : <div className="notice"><b>No publication records yet.</b> Configure a reconciled brokerage account to create its first card.</div>}
    {message && <div className="manage-provider-status" role="status">{message}</div>}
  </>;
}
