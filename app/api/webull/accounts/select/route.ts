import {
  authorizeWebullOwner,
  jsonResponse,
  proxyWebullJson,
  readJsonBody,
  requestBodyErrorResponse,
  webullStatusResponse,
} from "../../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  try {
    const body = await readJsonBody(request, { required: true });
    const accountId = body.accountId;
    if (
      typeof accountId !== "string" ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(accountId)
    ) {
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
