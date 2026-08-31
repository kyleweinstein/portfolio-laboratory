import { notFound } from "next/navigation";
import { requirePortfolioOwner } from "../../../discord-page-auth";
import {
  isOpaquePublicationId,
  isPortfolioSlug,
  loadManagedPublicationPreview,
  loadPublishedPortfolioDetail,
} from "../../../publication-server";
import { PortfolioDetailView, PortfolioMasthead } from "../../../publication-ui";

export const dynamic = "force-dynamic";

export default async function PublicationPrivacyPreview({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ slug?: string }>;
}) {
  const { slug: publicationId } = await params;
  const query = await searchParams;
  if (!isOpaquePublicationId(publicationId)) notFound();
  const fallbackSlug = typeof query.slug === "string" && isPortfolioSlug(query.slug)
    ? query.slug
    : null;
  await requirePortfolioOwner(`/manage/preview/${publicationId}${fallbackSlug ? `?slug=${encodeURIComponent(fallbackSlug)}` : ""}`);
  const portfolio = await loadManagedPublicationPreview(publicationId).catch(() => null) ??
    (fallbackSlug ? await loadPublishedPortfolioDetail(fallbackSlug).catch(() => null) : null);
  if (!portfolio) notFound();
  return <main className="publication-main privacy-preview">
    <PortfolioMasthead eyebrow="OWNER PRIVACY PREVIEW" title={portfolio.title} description="This page renders the exact allowlisted follower data contract without requiring Discord membership. It contains no account values, quantities, position values, cash amounts, or brokerage identifiers."/>
    <div className="privacy-preview-banner"><strong>Owner-only preview.</strong> Confirm the visible weights, per-share basis, percentage returns, and labels before publishing.</div>
    <a className="secondary publication-back-link" href="/manage">← Manage portfolios</a>
    <PortfolioDetailView portfolio={portfolio}/>
  </main>;
}
