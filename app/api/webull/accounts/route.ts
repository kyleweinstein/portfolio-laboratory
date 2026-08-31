import { jsonResponse } from "../../../webull-server";

export async function GET() {
  return jsonResponse({ error: "Use the redacted Webull status endpoint." }, 404);
}
