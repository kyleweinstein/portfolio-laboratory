import {
  isPortfolioSlug,
  loadPublishedPortfolioDetail,
} from "../../../publication-server";
import {
  authorizeDiscordViewerRequest,
  viewerJsonResponse,
} from "../../../viewer-server";

export async function GET(
  request: Request,
  context: { params: Promise<{ slug: string }> },
) {
  const access = await authorizeDiscordViewerRequest(request);
  if (!access.ok) return access.response;
  const { slug } = await context.params;
  if (!isPortfolioSlug(slug)) {
    return viewerJsonResponse(
      { error: "The published portfolio was not found." },
      404,
      access.refreshedCookie,
    );
  }
  try {
    const portfolio = await loadPublishedPortfolioDetail(slug);
    if (!portfolio) {
      return viewerJsonResponse(
        { error: "The published portfolio was not found." },
        404,
        access.refreshedCookie,
      );
    }
    return viewerJsonResponse(
      { portfolio },
      200,
      access.refreshedCookie,
    );
  } catch {
    return viewerJsonResponse(
      { error: "The published portfolio is temporarily unavailable." },
      503,
      access.refreshedCookie,
    );
  }
}
