"use client";

// fixture:tenants-page-v1
const tenants = [
  { id: "t_1", name: "Acme Corp", plan: "Enterprise", mrr: 4200, status: "active" },
  { id: "t_2", name: "Globex", plan: "Pro", mrr: 990, status: "active" },
  { id: "t_3", name: "Initech", plan: "Starter", mrr: 49, status: "past_due" },
];

export default function TenantsPage({ user }: { user: { role: string } }) {
  if (user.role !== "admin") return <p>Not allowed</p>;
  return (
    <table>
      <thead>
        <tr><th>Tenant</th><th>Plan</th><th>MRR</th><th>Status</th><th /></tr>
      </thead>
      <tbody>
        {tenants.map((tenant) => (
          <tr key={tenant.id}>
            <td>{tenant.name}</td>
            <td>{tenant.plan}</td>
            <td>${tenant.mrr}</td>
            <td>{tenant.status}</td>
            <td>
              <button onClick={() => alert(`Suspended ${tenant.name}`)}>Suspend</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
