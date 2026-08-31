import { requirePortfolioOwner } from "../discord-page-auth";
import {
  loadManagedPublications,
  loadProviderCapabilities,
  loadProviderAccounts,
  type ManagedPublication,
  type OwnerProviderAccount,
  type ProviderCapability,
} from "../publication-server";
import { PortfolioMasthead } from "../publication-ui";
import ManageProviders from "../manage-providers";
import ManagePublications from "../manage-publications";

export const dynamic = "force-dynamic";

export default async function ManagePortfoliosPage() {
  const owner = await requirePortfolioOwner("/manage");
  let publications: ManagedPublication[] = [];
  let providers: ProviderCapability[] = [];
  let accounts: OwnerProviderAccount[] = [];
  let unavailable = false;
  try {
    [publications, providers, accounts] = await Promise.all([
      loadManagedPublications(),
      loadProviderCapabilities(),
      Promise.all([
        loadProviderAccounts("webull").catch(() => []),
        loadProviderAccounts("plaid_m1").catch(() => []),
      ]).then(groups => groups.flat()),
    ]);
  } catch {
    unavailable = true;
  }
  return <main className="publication-main">
    <PortfolioMasthead eyebrow="OWNER CONTROL" title="MANAGE PORTFOLIOS" description="Connect read-only sources, review reconciliation quality, and decide which after-close snapshots are visible to Discord members."/>
    <ManageProviders providers={providers} csrfToken={owner.csrfToken}/>
    <section className="card manage-publications">
      <div className="section-title"><div><span className="eyebrow">After-close publication</span><h2>Portfolio cards</h2></div><span className="pill">Owner @{owner.login}</span></div>
      {unavailable
        ? <div className="notice error"><b>Publication controls are unavailable.</b> The existing public revision has not been changed.</div>
        : <ManagePublications initialPublications={publications} accounts={accounts} csrfToken={owner.csrfToken}/>
      }
      <p className="note">Publication is atomic. A failed broker sync, incomplete cash-flow history, weight mismatch, or analytics failure preserves the last successful follower revision.</p>
    </section>
    <section className="card manage-publications operator-workflow">
      <span className="eyebrow">Private operator workflow</span>
      <h2>Reconcile privately, then publish atomically</h2>
      <p>
        Statement files and private account values never pass through this browser.
        Use the checked-in M1 statement tool from a private folder outside every Git
        worktree, with its encrypted bundle mode and a locally held key.
      </p>
      <ol>
        <li><b>Prepare.</b> Download the M1 or Apex statements privately and create an encrypted normalized bundle. Keep the original PDFs until the extracted dates, values, cash or margin, and source hashes have been independently checked.</li>
        <li><b>Reconcile.</b> Validate the normalized records against Plaid and broker snapshots, then import only verified statement anchors through the private service. Delete temporary PDFs only after the import is confirmed.</li>
        <li><b>Publish.</b> Sync the provider, configure the card, and use <b>Privacy preview</b> before <b>Publish</b>. The server builds the allowlisted percentage-only projection and commits it atomically; a failed run leaves the previous follower revision in place.</li>
      </ol>
      <p className="note">
        This management page intentionally has no statement upload or raw analytics
        editor. Benchmark and publication settings are stored server-side, and
        published analytics are generated from the reconciled private records.
      </p>
    </section>
    <footer>Owner controls use the GitHub allowlist. Follower pages use independent Discord server-membership authorization.</footer>
  </main>;
}
