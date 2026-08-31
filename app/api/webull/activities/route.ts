import { jsonResponse } from "../../../webull-server";

export async function GET() {
  return jsonResponse({ error: "This raw financial-data endpoint is not available." }, 404);
}
