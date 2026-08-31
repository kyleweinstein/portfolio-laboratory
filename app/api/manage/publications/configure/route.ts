import { authorizePublicationOwner, ownerJsonResponse } from "../../../../owner-publication-server";
import { configureManagedPublication } from "../../../../publication-server";

const MAX_BODY_BYTES = 16_384;

export async function PUT(request: Request) {
  const access = await authorizePublicationOwner(request, true);
  if (!access.ok) return access.response;
  let value: Record<string, unknown>;
  try {
    const text = await request.text();
    if (text.length > MAX_BODY_BYTES) throw new Error("too large");
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("invalid");
    value = parsed as Record<string, unknown>;
  } catch {
    return ownerJsonResponse({ error: "The publication settings are invalid." }, 400);
  }
  if (
    typeof value.accountHandle !== "string" ||
    typeof value.slug !== "string" ||
    typeof value.title !== "string" ||
    typeof value.benchmarkSymbol !== "string" ||
    typeof value.enabled !== "boolean"
  ) {
    return ownerJsonResponse({ error: "The publication settings are invalid." }, 400);
  }
  try {
    return ownerJsonResponse(await configureManagedPublication({
      accountHandle: value.accountHandle,
      slug: value.slug,
      title: value.title,
      benchmarkSymbol: value.benchmarkSymbol,
      enabled: value.enabled,
    }));
  } catch {
    return ownerJsonResponse({ error: "The publication settings could not be saved." }, 422);
  }
}
