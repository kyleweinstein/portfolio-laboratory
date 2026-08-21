import {
  authorizeWebullOwner,
  jsonResponse,
  proxyWebullJson,
  readJsonBody,
  requestBodyErrorResponse,
} from "../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  try {
    const input = await readJsonBody(request);
    const body: Record<string, unknown> = {};
    for (const key of ["from", "to"] as const) {
      if (!(key in input)) continue;
      if (
        typeof input[key] !== "string" ||
        !/^\d{4}-\d{2}-\d{2}$/.test(input[key])
      ) {
        return jsonResponse({ error: `${key} must be an ISO date.` }, 400);
      }
      body[key] = input[key];
    }
    if ("force" in input) {
      if (typeof input.force !== "boolean") {
        return jsonResponse({ error: "force must be a boolean." }, 400);
      }
      body.force = input.force;
    }
    return proxyWebullJson("/backfill", access.session, {
      method: "POST",
      body,
      timeoutMs: 60_000,
    });
  } catch (caught) {
    return requestBodyErrorResponse(caught);
  }
}
