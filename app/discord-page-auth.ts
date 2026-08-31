import { headers } from "next/headers";
import { redirect } from "next/navigation";
import {
  inspectDiscordSession,
  safeDiscordReturnTo,
  type DiscordViewer,
} from "./discord-auth";
import { readGitHubSession, type GitHubSession } from "./github-auth";

export async function requireDiscordViewer(
  returnTo: string,
): Promise<DiscordViewer> {
  const safeReturnTo = safeDiscordReturnTo(returnTo);
  const request = await requestFromServerHeaders(safeReturnTo);
  const inspection = await inspectDiscordSession(request);
  if (inspection.state === "unauthenticated") {
    redirect(
      `/api/discord/auth/login?return_to=${encodeURIComponent(safeReturnTo)}`,
    );
    throw new Error("Discord sign-in redirect did not complete.");
  }
  if (inspection.state === "stale" || inspection.state === "unavailable") {
    redirect(
      `/api/discord/auth/verify?return_to=${encodeURIComponent(safeReturnTo)}`,
    );
    throw new Error("Discord membership recheck redirect did not complete.");
  }
  return inspection.viewer;
}

export async function requirePortfolioOwner(
  returnTo = "/manage",
): Promise<GitHubSession> {
  const safeReturnTo = safeDiscordReturnTo(returnTo);
  const request = await requestFromServerHeaders(safeReturnTo);
  const session = await readGitHubSession(request);
  if (!session) {
    redirect(
      `/api/owner/auth/login?return_to=${encodeURIComponent(safeReturnTo)}`,
    );
    throw new Error("GitHub owner sign-in redirect did not complete.");
  }
  return session;
}

async function requestFromServerHeaders(pathname: string): Promise<Request> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ??
    requestHeaders.get("host") ??
    "localhost";
  const protocol = requestHeaders.get("x-forwarded-proto") ??
    (host.includes("localhost") ? "http" : "https");
  const forwarded = new Headers();
  const cookie = requestHeaders.get("cookie");
  if (cookie) forwarded.set("cookie", cookie);
  return new Request(`${protocol}://${host}${pathname}`, { headers: forwarded });
}
