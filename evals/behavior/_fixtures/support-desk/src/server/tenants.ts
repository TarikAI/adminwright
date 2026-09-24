import { db } from "./db";

// fixture:tenants-v1
export async function listTenants(query: string) {
  return db.tenant.findMany({
    where: { name: { contains: query, mode: "insensitive" } },
    include: { subscription: true },
    orderBy: { createdAt: "desc" },
    take: 50,
  });
}

export async function suspendTenant(tenantId: string) {
  return db.tenant.update({ where: { id: tenantId }, data: { status: "SUSPENDED" } });
}
