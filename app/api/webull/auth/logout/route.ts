import {
  GITHUB_OAUTH_STATE_COOKIE,
  GITHUB_SESSION_COOKIE,
  expireSecureCookie,
} from "../../../../github-auth";
import {
  authorizeWebullOwner,
  jsonResponse,
} from "../../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  const response = jsonResponse({ authenticated: false });
  response.headers.append(
    "set-cookie",
    expireSecureCookie(GITHUB_SESSION_COOKIE),
  );
  response.headers.append(
    "set-cookie",
    expireSecureCookie(GITHUB_OAUTH_STATE_COOKIE),
  );
  return response;
}

export const DELETE = POST;
