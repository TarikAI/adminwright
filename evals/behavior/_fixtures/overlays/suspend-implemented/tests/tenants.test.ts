import { describe, expect, it, vi } from "vitest";
import { db } from "../src/server/db";
import { suspendTenant } from "../src/server/tenants";

describe("tenant.suspend", () => {
  it("marks the tenant SUSPENDED", async () => {
    const update = vi.spyOn(db.tenant, "update").mockResolvedValue({ id: "t_1", status: "SUSPENDED" } as never);
    await suspendTenant("t_1");
    expect(update).toHaveBeenCalledWith({ where: { id: "t_1" }, data: { status: "SUSPENDED" } });
  });
});
