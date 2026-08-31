import {
  GITHUB_CSRF_HEADER,
  readGitHubSession,
  validateMutationRequest,
  type GitHubSession,
} from "./github-auth";

export type PublicationOwnerAccess =
  | { ok: true; session: GitHubSession }
  | { ok: false; response: Response };

export async function authorizePublicationOwner(
  request: Request,
  mutation = false,
): Promise<PublicationOwnerAccess> {
  const session = await readGitHubSession(request);
  if (!session) {
    return {
      ok: false,
      response: ownerJsonResponse(
        {
          error: "Portfolio Lab owner sign-in is required.",
          signInUrl: "/api/owner/auth/login?return_to=%2Fmanage",
        },
        401,
      ),
    };
  }
  if (mutation) {
    const validation = validateMutationRequest(request, session);
    if (!validation.ok) {
      return {
        ok: false,
        response: ownerJsonResponse({ error: validation.error }, 403),
      };
    }
  }
  return { ok: true, session };
}

export function ownerJsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "cache-control": "private, no-store, max-age=0",
      pragma: "no-cache",
      "referrer-policy": "no-referrer",
      vary: "Cookie",
    },
  });
}

export function ownerSessionEnvelope(session: GitHubSession): {
  csrfToken: string;
  csrfHeader: typeof GITHUB_CSRF_HEADER;
} {
  return { csrfToken: session.csrfToken, csrfHeader: GITHUB_CSRF_HEADER };
}
