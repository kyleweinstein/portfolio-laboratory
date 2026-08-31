import { authorizePublicationOwner, ownerJsonResponse } from "../../../../../owner-publication-server";
import { createPlaidLinkToken } from "../../../../../publication-server";

export async function POST(request: Request) {
  const access = await authorizePublicationOwner(request, true);
  if (!access.ok) return access.response;
  try {
    return ownerJsonResponse(await createPlaidLinkToken());
  } catch {
    return ownerJsonResponse({ error: "Plaid Link could not be started." }, 503);
  }
}
