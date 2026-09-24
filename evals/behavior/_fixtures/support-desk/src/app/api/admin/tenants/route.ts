import { listTenants } from "@/server/tenants";

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams.get("q") ?? "";
  return Response.json(await listTenants(query));
}
