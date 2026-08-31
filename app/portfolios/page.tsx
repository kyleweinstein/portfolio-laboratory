import { requireDiscordViewer } from "../discord-page-auth";
import { loadPublishedPortfolioCards } from "../publication-server";
import { PortfolioCards, PortfolioMasthead } from "../publication-ui";

export const dynamic = "force-dynamic";

export default async function PublishedPortfoliosPage() {
  await requireDiscordViewer("/portfolios");
  let portfolios: Awaited<ReturnType<typeof loadPublishedPortfolioCards>> = [];
  let unavailable = false;
  try {
    portfolios = await loadPublishedPortfolioCards();
  } catch {
    unavailable = true;
  }

  return <main className="publication-main">
    <PortfolioMasthead
      eyebrow="THE SEER'S"
      title="TRACKED PORTFOLIOS"
      description="Reconciled performance, signed holdings, and risk analytics shared with members of the community. Dollar account values remain private."
    />
    {unavailable
      ? <section className="notice error" role="alert"><b>Tracked portfolios are temporarily unavailable.</b> Membership was verified, but the latest published snapshot could not be loaded. Try again shortly.</section>
      : <PortfolioCards portfolios={portfolios}/>
    }
    <footer>Published analytics are educational and are not investment, tax, accounting, or legal advice. Holdings and performance may be delayed.</footer>
  </main>;
}
