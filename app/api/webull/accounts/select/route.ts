import {
  authorizeWebullOwner,
  jsonResponse,
  proxyWebullJson,
  readJsonBody,
  requestBodyErrorResponse,
  resolveWebullAccountReference,
  webullStatusResponse,
} from "../../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  try {
    const body = await readJsonBody(request, { required: true });
    const accountRef = body.accountRef;
    if (
      typeof accountRef !== "string" ||
      !/^wbr_[A-Za-z0-9_-]{24,64}$/.test(accountRef)
    ) {
      return jsonResponse({ error: "Select a valid Webull account." }, 400);
    }
    const accountId = await resolveWebullAccountReference(accountRef, access.session);
    if (!accountId) {
      return jsonResponse({ error: "Select a valid Webull account." }, 400);
    }
    const response = await proxyWebullJson("/accounts/select", access.session, {
      method: "POST",
      body: { accountId },
    });
    return response.ok ? webullStatusResponse(request) : response;
  } catch (caught) {
    return requestBodyErrorResponse(caught);
  }
}
