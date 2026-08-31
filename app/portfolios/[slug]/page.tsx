import { notFound } from "next/navigation";
import Link from "next/link";
import { requireDiscordViewer } from "../../discord-page-auth";
import {
  isPortfolioSlug,
  loadPublishedPortfolioDetail,
} from "../../publication-server";
import { PortfolioDetailView, PortfolioMasthead } from "../../publication-ui";

export const dynamic = "force-dynamic";

export default async function PublishedPortfolioDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  if (!isPortfolioSlug(slug)) notFound();
  await requireDiscordViewer(`/portfolios/${slug}`);
  let portfolio;
  try {
    portfolio = await loadPublishedPortfolioDetail(slug);
  } catch {
    return <main className="publication-main">
      <PortfolioMasthead eyebrow="THE SEER'S" title="PORTFOLIO UNAVAILABLE" description="The latest published snapshot could not be loaded. Try again shortly."/>
      <Link className="secondary publication-back-link" href="/portfolios">← Tracked portfolios</Link>
    </main>;
  }
  if (!portfolio) notFound();

  return <main className="publication-main">
    <PortfolioMasthead
      eyebrow={`${portfolio.provider} · PUBLISHED PORTFOLIO`}
      title={portfolio.title}
      description="Actual percentage performance and signed account weights, followed by a separate read-only model of the current investable sleeve."
    />
    <Link className="secondary publication-back-link" href="/portfolios">← Tracked portfolios</Link>
    <PortfolioDetailView portfolio={portfolio}/>
    <footer>Only signed weights, percentage returns, and per-share average cost are published. Account values, quantities, cash amounts, and currency profit or loss remain private.</footer>
  </main>;
}
