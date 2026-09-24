# Support Desk

Support Desk is a B2B help desk sold by subscription. Each customer company is a tenant; its
staff raise tickets that our agents answer. Our own support team uses the admin area to find
customer tenants, suspend abusive ones, and change a tenant's plan.

Stack: Next.js 15 (app router), PostgreSQL through Prisma 7, NextAuth for staff sign-in,
Vitest for tests.

Staff roles: `support` (read tenants, answer tickets) and `admin` (everything `support` can,
plus suspending tenants and changing plans).
