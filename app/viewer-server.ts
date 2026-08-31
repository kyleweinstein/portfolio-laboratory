import {
  discordSessionCookie,
  expireDiscordSessionCookie,
  revalidateDiscordSession,
  type DiscordViewer,
} from "./discord-auth";
import { assertViewerSafeShape } from "./publication-server";

export type ViewerAccess =
  | {
      ok: true;
      viewer: DiscordViewer;
      refreshedCookie: string | null;
    }
  | { ok: false; response: Response };

export async function authorizeDiscordViewerRequest(
  request: Request,
): Promise<ViewerAccess> {
  const result = await revalidateDiscordSession(request);
  if (result.ok) {
    return {
      ok: true,
      viewer: result.viewer,
      refreshedCookie: result.cookieValue
        ? discordSessionCookie(result.cookieValue, result.cookieMaxAgeSeconds)
        : null,
    };
  }
  const message = result.reason === "unauthenticated"
    ? "Sign in with Discord to view published portfolios."
    : result.reason === "not_a_member"
      ? "Membership in the configured Discord server is required."
      : "Discord membership could not be verified. Try again shortly.";
  const body = result.reason === "unauthenticated"
    ? {
        error: message,
        signInUrl: "/api/discord/auth/login?return_to=%2Fportfolios",
      }
    : { error: message };
  const response = viewerJsonResponse(body, result.status);
  if (result.clearCookie) {
    response.headers.append("set-cookie", expireDiscordSessionCookie());
  }
  return { ok: false, response };
}

export function viewerJsonResponse(
  body: unknown,
  status = 200,
  refreshedCookie: string | null = null,
): Response {
  assertViewerSafeShape(body);
  const response = Response.json(body, {
    status,
    headers: viewerResponseHeaders(),
  });
  if (refreshedCookie) response.headers.append("set-cookie", refreshedCookie);
  return response;
}

export function viewerResponseHeaders(): HeadersInit {
  return {
    "cache-control": "private, no-store, max-age=0",
    pragma: "no-cache",
    "referrer-policy": "no-referrer",
    vary: "Cookie",
  };
}
