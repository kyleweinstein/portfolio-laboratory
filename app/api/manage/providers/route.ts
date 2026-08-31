import { authorizePublicationOwner, ownerJsonResponse, ownerSessionEnvelope } from "../../../owner-publication-server";
import { loadProviderCapabilities } from "../../../publication-server";

export async function GET(request: Request) {
  const access = await authorizePublicationOwner(request);
  if (!access.ok) return access.response;
  try {
    const providers = await loadProviderCapabilities();
    return ownerJsonResponse({ providers, ...ownerSessionEnvelope(access.session) });
  } catch {
    return ownerJsonResponse(
      { error: "Broker provider status is temporarily unavailable.", ...ownerSessionEnvelope(access.session) },
      503,
    );
  }
}
