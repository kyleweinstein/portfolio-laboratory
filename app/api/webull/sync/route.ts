import {
  authorizeWebullOwner,
  jsonResponse,
  proxyWebullJson,
  readJsonBody,
  requestBodyErrorResponse,
  resolveWebullAccountReference,
  webullStatusResponse,
} from "../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  try {
    const input = await readJsonBody(request);
    const accountRef = input.accountRef;
    if (
      accountRef !== undefined &&
      (typeof accountRef !== "string" ||
        !/^wbr_[A-Za-z0-9_-]{24,64}$/.test(accountRef))
    ) {
      return jsonResponse({ error: "Select a valid Webull account." }, 400);
    }
    const accountId = typeof accountRef === "string"
      ? await resolveWebullAccountReference(accountRef, access.session)
      : null;
    if (typeof accountRef === "string" && !accountId) {
      return jsonResponse({ error: "Select a valid Webull account." }, 400);
    }
    const response = await proxyWebullJson("/sync", access.session, {
      method: "POST",
      body: accountId ? { accountId } : {},
      timeoutMs: 60_000,
    });
    return response.ok ? webullStatusResponse(request) : response;
  } catch (caught) {
    return requestBodyErrorResponse(caught);
  }
}
