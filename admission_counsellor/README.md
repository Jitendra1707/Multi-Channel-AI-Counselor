# Admission Counsellor — AI Virtual Humanoid Counselor Platform

An enterprise, multi-tenant SaaS prototype implementing the **Admission Counsellor Product Design & Implementation Blueprint** — a responsible, institution-controlled AI admissions operating system. The AI counselor ("Aria") speaks only from institution-approved knowledge, always discloses it is an AI, never invents fees/scholarships/placements, and escalates to humans when confidence is low or a matter is sensitive.

Built with **Angular 19** (standalone components + Signals), a token-driven design system, and a signal-based mock-data layer.

## Run

```bash
npm install           # first time
npm start             # dev server → http://localhost:4200 (or `ng serve`)
npm run build         # production build → dist/admission-counsellor
```

> No backend required — the prototype runs entirely against realistic mocked data (`src/app/data-access`).

## What's inside

**Public**
- `/` — premium marketing landing portal (hero, responsible-AI band, feature narrative, security, integrations)
- `/request-demo` — two-pane demo request with success state
- `/login` — split-screen sign-in (Password / SSO / OTP) with role-based routing
- `/onboarding` — 14-step institution onboarding wizard with a live AI-Readiness Checklist

**Authenticated workspace (`/app/*`)** — role-filtered shell with sidebar, topbar (global ⌘K search, institution switcher, admission-cycle selector, AI-counselor status chip, notifications, theme toggle), and breadcrumbs:
- **Overview** dashboard — KPIs, admissions funnel, demand/region analytics, live activity, handoffs
- **AI Counselor Workbench** — profile / behavior (per-mode, approval-chipped) / performance
- **CRM** — lead list (saved views, bulk actions, peek drawer), Excel import wizard, 360° candidate profile + journey timeline
- **Communications** — unified center, live voice console, WhatsApp console, email campaign console
- **Knowledge (KMS)** — document library, upload with AI readiness checks, document approval + diff
- **Governance** — guardrails (always/never), approvals queue, learning review (knowledge gaps), audit logs
- **Human Handoff** — prioritized queue with a non-negotiable emotional-distress care path
- **Outcomes** — applications tracking, reference conversion, live monitor, analytics & insights
- **Admin** — settings (users, RBAC permission matrix, channels, consent), integrations

V-Cons (avatar meetings) and AI-narrated Management Reporting are presented as Phase-2 previews.

## Architecture

```
src/app/
  core/          auth, theme, toast services + layout (shell, sidebar, topbar, command palette)
  domain/        TypeScript models (§37 of the blueprint)
  data-access/   seeded mock data + signal-based DataStore
  shared/ui/     design-system components (icons, badges, charts, funnel, metric cards, timeline, drawer, …)
  features/      one folder per screen (lazy-loaded standalone components)
  app.routes.ts  full route map (§7.2)
src/styles.scss  design tokens (§34): palette (light/dark), typography, spacing, elevation, sentiment/probability scales
```

Theming is runtime-switchable via CSS custom properties; light/dark and per-tenant branding are supported. Accessibility: semantic landmarks, focus-visible states, `prefers-reduced-motion` respected, AA-oriented contrast tokens.
