import { webullStatusResponse } from "../../../webull-server";

export async function GET(request: Request) {
  return webullStatusResponse(request);
}
