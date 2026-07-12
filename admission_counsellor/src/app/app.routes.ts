import { Routes } from '@angular/router';
import { StubPageComponent } from './shared/ui/stub-page.component';

const stub = (data: any) => ({ component: StubPageComponent, data: { stub: data } });

export const routes: Routes = [
  // ---- Public / unauthenticated ----
  { path: '', loadComponent: () => import('./features/landing/landing.component').then(m => m.LandingComponent), title: 'Admission Counsellor — AI Virtual Humanoid Counselors' },
  { path: 'request-demo', loadComponent: () => import('./features/landing/request-demo.component').then(m => m.RequestDemoComponent), title: 'Request a demo — Admission Counsellor' },
  { path: 'login', loadComponent: () => import('./features/auth/sign-in.component').then(m => m.SignInComponent), title: 'Sign in — Admission Counsellor' },
  { path: 'onboarding', loadComponent: () => import('./features/onboarding/onboarding.component').then(m => m.OnboardingComponent), title: 'Onboarding — Admission Counsellor' },
  // Public meeting join — a shared meeting link lands here (no auth/shell).
  // Anyone with the link enters their name and joins (Google-Meet style).
  { path: 'meeting/:room', loadComponent: () => import('./features/meetings/join.component').then(m => m.JoinComponent), title: 'Join meeting — Admission Counsellor' },

  // ---- Authenticated shell ----
  {
    path: 'app',
    loadComponent: () => import('./core/layout/shell.component').then(m => m.ShellComponent),
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'overview' },
      { path: 'overview', loadComponent: () => import('./features/overview/overview.component').then(m => m.OverviewComponent), title: 'Overview — Admission Counsellor' },

      { path: 'ai-counselor', loadComponent: () => import('./features/ai-counselor/ai-counselor.component').then(m => m.AiCounselorComponent), title: 'AI Counselor Workbench — Admission Counsellor' },

      { path: 'crm', loadComponent: () => import('./features/crm/crm-list.component').then(m => m.CrmListComponent), title: 'CRM Leads — Admission Counsellor' },
      { path: 'crm/import', loadComponent: () => import('./features/crm/crm-import.component').then(m => m.CrmImportComponent), title: 'Import leads — Admission Counsellor' },
      { path: 'crm/candidate/:id', loadComponent: () => import('./features/crm/candidate-profile.component').then(m => m.CandidateProfileComponent), title: 'Candidate — Admission Counsellor' },

      // ---- Career Counselor (Vera) screens ----
      { path: 'career/pathways', loadComponent: () => import('./features/career/career-pathways.component').then(m => m.CareerPathwaysComponent), title: 'Career Pathways — Admission Counsellor' },
      { path: 'career/skills', loadComponent: () => import('./features/career/career-skills.component').then(m => m.CareerSkillsComponent), title: 'Skills & Upskilling — Admission Counsellor' },
      { path: 'career/mentors', loadComponent: () => import('./features/career/career-mentors.component').then(m => m.CareerMentorsComponent), title: 'Mentors & Placements — Admission Counsellor' },

      { path: 'meetings', loadComponent: () => import('./features/meetings/meetings.component').then(m => m.MeetingsComponent), title: 'Meetings & Calendar — Admission Counsellor' },
      { path: 'communications', loadComponent: () => import('./features/communications/communications.component').then(m => m.CommunicationsComponent), title: 'Communications — Admission Counsellor' },
      { path: 'communications/voice', loadComponent: () => import('./features/communications/voice-console.component').then(m => m.VoiceConsoleComponent), title: 'Voice console — Admission Counsellor' },
      { path: 'communications/whatsapp', loadComponent: () => import('./features/communications/whatsapp-console.component').then(m => m.WhatsappConsoleComponent), title: 'WhatsApp — Admission Counsellor' },
      { path: 'communications/whatsapp/:candidateId', loadComponent: () => import('./features/communications/whatsapp-console.component').then(m => m.WhatsappConsoleComponent), title: 'WhatsApp — Admission Counsellor' },
      { path: 'communications/email', loadComponent: () => import('./features/communications/email-console.component').then(m => m.EmailConsoleComponent), title: 'Email campaigns — Admission Counsellor' },

      { path: 'kms', loadComponent: () => import('./features/kms/kms-library.component').then(m => m.KmsLibraryComponent), title: 'Knowledge Management — Admission Counsellor' },
      { path: 'kms/upload', loadComponent: () => import('./features/kms/kms-upload.component').then(m => m.KmsUploadComponent), title: 'Upload document — Admission Counsellor' },
      { path: 'kms/document/:id', loadComponent: () => import('./features/kms/kms-detail.component').then(m => m.KmsDetailComponent), title: 'Document — Admission Counsellor' },

      { path: 'knowledge-review', loadComponent: () => import('./features/knowledge-review/knowledge-review.component').then(m => m.KnowledgeReviewComponent), title: 'Knowledge review — Admission Counsellor' },

      { path: 'approvals', loadComponent: () => import('./features/approvals/approvals.component').then(m => m.ApprovalsComponent), title: 'Approvals — Admission Counsellor' },
      { path: 'approvals/:id', loadComponent: () => import('./features/approvals/approvals.component').then(m => m.ApprovalsComponent), title: 'Approval — Admission Counsellor' },

      { path: 'handoff', loadComponent: () => import('./features/handoff/handoff.component').then(m => m.HandoffComponent), title: 'Human Handoff — Admission Counsellor' },
      { path: 'handoff/workspace/:candidateId', loadComponent: () => import('./features/handoff/handoff.component').then(m => m.HandoffComponent), title: 'Counselor workspace — Admission Counsellor' },

      { path: 'applications', loadComponent: () => import('./features/applications/applications.component').then(m => m.ApplicationsComponent), title: 'Applications — Admission Counsellor' },
      { path: 'references', loadComponent: () => import('./features/references/references.component').then(m => m.ReferencesComponent), title: 'References — Admission Counsellor' },
      { path: 'live-monitor', loadComponent: () => import('./features/live-monitor/live-monitor.component').then(m => m.LiveMonitorComponent), title: 'Live Monitor — Admission Counsellor' },
      { path: 'analytics', loadComponent: () => import('./features/analytics/analytics.component').then(m => m.AnalyticsComponent), title: 'Analytics — Admission Counsellor' },
      { path: 'campaigns', loadComponent: () => import('./features/campaigns/campaigns.component').then(m => m.CampaignsComponent), title: 'Campaigns — Admission Counsellor' },
      { path: 'guardrails', loadComponent: () => import('./features/guardrails/guardrails.component').then(m => m.GuardrailsComponent), title: 'Guardrails — Admission Counsellor' },
      { path: 'learning-review', loadComponent: () => import('./features/learning-review/learning-review.component').then(m => m.LearningReviewComponent), title: 'Learning Review — Admission Counsellor' },
      { path: 'audit-logs', loadComponent: () => import('./features/audit-logs/audit-logs.component').then(m => m.AuditLogsComponent), title: 'Audit Logs — Admission Counsellor' },
      { path: 'settings', loadComponent: () => import('./features/settings/settings.component').then(m => m.SettingsComponent), title: 'Settings — Admission Counsellor' },
      { path: 'settings/:section', loadComponent: () => import('./features/settings/settings.component').then(m => m.SettingsComponent), title: 'Settings — Admission Counsellor' },
      { path: 'integrations', loadComponent: () => import('./features/integrations/integrations.component').then(m => m.IntegrationsComponent), title: 'Integrations — Admission Counsellor' },
      { path: 'licensing', loadComponent: () => import('./features/billing/licensing.component').then(m => m.LicensingComponent), title: 'Plan & Licensing — Admission Counsellor' },
      { path: 'billing', loadComponent: () => import('./features/billing/billing.component').then(m => m.BillingComponent), title: 'Billing & Usage — Admission Counsellor' },

      // ---- V-Cons: real WebRTC avatar briefing (AegisBackend) ----
      { path: 'vcons', loadComponent: () => import('./features/vcons/vcons.component').then(m => m.VconsComponent), title: 'V-Cons — Admission Counsellor' },

      // ---- Phase 2 ----
      { path: 'reports', ...stub({ title: 'Management Reporting', subtitle: 'AI-narrated board presentations', icon: 'presentation', phase2: true,
          ref: '§30, §32.32–32.33', blurb: 'AI avatar presenter, slide-style dashboards, voice narration, boardroom mode and an "Ask the Counselor" Q&A interface.',
          features: ['AI-narrated performance summary', 'Forecasted admissions', 'Export to PDF / PowerPoint', 'Drill from slide into live data'] }) },
    ],
  },

  { path: '**', redirectTo: '' },
];
