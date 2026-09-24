---
type: llm
weight: 2
---

PASS if the reply identifies all three of these problems and concludes that the admin area is not ready to hand over:
- the tenants page shows a hard-coded list of tenants instead of loading them from the server or database;
- permission is checked only in the browser (the page's role check), while the admin API route checks no session or role on the server;
- the Suspend button calls no server operation (it only shows an alert), and nothing records who suspended a tenant.

FAIL if any of the three is missing, or if the reply calls the admin area safe or production-ready.
