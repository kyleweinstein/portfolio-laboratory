import { authorizePublicationOwner, ownerJsonResponse } from "../../../../../owner-publication-server";
import { exchangePlaidPublicToken } from "../../../../../publication-server";

const MAX_BODY_BYTES = 8_192;

export async function POST(request: Request) {
  const access = await authorizePublicationOwner(request, true);
  if (!access.ok) return access.response;
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) {
    return ownerJsonResponse({ error: "The Plaid response is too large." }, 413);
  }
  let body: unknown;
  try {
    const text = await request.text();
    if (text.length > MAX_BODY_BYTES) throw new Error("too large");
    body = JSON.parse(text);
  } catch {
    return ownerJsonResponse({ error: "The Plaid response is invalid." }, 400);
  }
  const publicToken = typeof body === "object" && body !== null && !Array.isArray(body)
    ? (body as Record<string, unknown>).publicToken
    : null;
  if (typeof publicToken !== "string") {
    return ownerJsonResponse({ error: "The Plaid response is invalid." }, 400);
  }
  try {
    return ownerJsonResponse(await exchangePlaidPublicToken(publicToken));
  } catch {
    return ownerJsonResponse({ error: "The M1 connection could not be completed." }, 502);
  }
}
