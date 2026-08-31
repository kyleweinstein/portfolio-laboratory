import { loadPublishedPortfolioCards } from "../../publication-server";
import {
  authorizeDiscordViewerRequest,
  viewerJsonResponse,
} from "../../viewer-server";

export async function GET(request: Request) {
  const access = await authorizeDiscordViewerRequest(request);
  if (!access.ok) return access.response;
  try {
    const portfolios = await loadPublishedPortfolioCards();
    return viewerJsonResponse(
      { portfolios },
      200,
      access.refreshedCookie,
    );
  } catch {
    return viewerJsonResponse(
      { error: "Published portfolios are temporarily unavailable." },
      503,
      access.refreshedCookie,
    );
  }
}
