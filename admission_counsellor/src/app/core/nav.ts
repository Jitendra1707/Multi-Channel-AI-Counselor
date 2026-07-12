import { CounselorType } from './counselor.service';

export interface NavItem {
  label: string;
  icon: string;
  route: string;
  group: string;
  badge?: 'escalations' | 'approvals' | 'gaps';
  adminOnly?: boolean;
}

/** Admin group — gated to tenant-admin roles, rendered at the bottom of the sidebar. */
const ADMIN: NavItem[] = [
  { label: 'Plan & Licensing', icon: 'award', route: '/app/licensing', group: 'Admin', adminOnly: true },
  { label: 'Billing & Usage', icon: 'receipt', route: '/app/billing', group: 'Admin', adminOnly: true },
  { label: 'Counselor', icon: 'bot', route: '/app/ai-counselor', group: 'Admin', adminOnly: true },
  { label: 'Integrations', icon: 'plug', route: '/app/integrations', group: 'Admin', adminOnly: true },
  { label: 'Settings', icon: 'settings', route: '/app/settings', group: 'Admin', adminOnly: true },
];

const GOV = (kmsLabel: string): NavItem[] => [
  { label: kmsLabel, icon: 'book-open', route: '/app/kms', group: 'Knowledge & governance' },
  { label: 'Learning Review', icon: 'brain', route: '/app/learning-review', group: 'Knowledge & governance', badge: 'gaps' },
  { label: 'Guardrails', icon: 'shield-check', route: '/app/guardrails', group: 'Knowledge & governance' },
  { label: 'Approvals', icon: 'clipboard-check', route: '/app/approvals', group: 'Knowledge & governance', badge: 'approvals' },
  { label: 'Knowledge Review', icon: 'sparkles', route: '/app/knowledge-review', group: 'Knowledge & governance' },
  { label: 'Audit Logs', icon: 'scroll-text', route: '/app/audit-logs', group: 'Knowledge & governance' },
];

const NAV_ADMISSION: NavItem[] = [
  { label: 'Overview', icon: 'layout-dashboard', route: '/app/overview', group: 'Workspace' },
  { label: 'CRM Leads', icon: 'users', route: '/app/crm', group: 'Leads & data' },
  { label: 'Applications', icon: 'file-text', route: '/app/applications', group: 'Leads & data' },
  { label: 'References', icon: 'git-branch', route: '/app/references', group: 'Leads & data' },
  { label: 'Conversations', icon: 'message-square', route: '/app/communications', group: 'Communication' },
  { label: 'Meetings & Calendar', icon: 'calendar', route: '/app/meetings', group: 'Communication' },
  { label: 'V-Cons', icon: 'video', route: '/app/vcons', group: 'Communication' },
  { label: 'Campaigns', icon: 'megaphone', route: '/app/campaigns', group: 'Communication' },
  ...GOV('Knowledge (KMS)'),
  { label: 'Live Monitor', icon: 'activity', route: '/app/live-monitor', group: 'Intelligence' },
  { label: 'Admission Analytics', icon: 'bar-chart', route: '/app/analytics', group: 'Intelligence' },
  { label: 'Reports', icon: 'presentation', route: '/app/reports', group: 'Intelligence' },
  { label: 'Human Handoff', icon: 'headphones', route: '/app/handoff', group: 'Intelligence', badge: 'escalations' },
  ...ADMIN,
];

const NAV_CAREER: NavItem[] = [
  { label: 'Overview', icon: 'layout-dashboard', route: '/app/overview', group: 'Workspace' },
  { label: 'Students', icon: 'users', route: '/app/crm', group: 'Students & pathways' },
  { label: 'Career Pathways', icon: 'route', route: '/app/career/pathways', group: 'Students & pathways' },
  { label: 'Skills & Upskilling', icon: 'lightbulb', route: '/app/career/skills', group: 'Students & pathways' },
  { label: 'Mentors & Placements', icon: 'briefcase', route: '/app/career/mentors', group: 'Students & pathways' },
  { label: 'Conversations', icon: 'message-square', route: '/app/communications', group: 'Communication' },
  { label: 'Meetings & Calendar', icon: 'calendar', route: '/app/meetings', group: 'Communication' },
  { label: 'Career Webinars', icon: 'video', route: '/app/vcons', group: 'Communication' },
  { label: 'Outreach Campaigns', icon: 'megaphone', route: '/app/campaigns', group: 'Communication' },
  ...GOV('Career Knowledge'),
  { label: 'Live Monitor', icon: 'activity', route: '/app/live-monitor', group: 'Intelligence' },
  { label: 'Career Analytics', icon: 'bar-chart', route: '/app/analytics', group: 'Intelligence' },
  { label: 'Reports', icon: 'presentation', route: '/app/reports', group: 'Intelligence' },
  { label: 'Human Handoff', icon: 'headphones', route: '/app/handoff', group: 'Intelligence', badge: 'escalations' },
  ...ADMIN,
];

const GROUPS_ADMISSION = ['Workspace', 'Leads & data', 'Communication', 'Knowledge & governance', 'Intelligence', 'Admin'];
const GROUPS_CAREER = ['Workspace', 'Students & pathways', 'Communication', 'Knowledge & governance', 'Intelligence', 'Admin'];

export function navFor(type: CounselorType): NavItem[] { return type === 'career' ? NAV_CAREER : NAV_ADMISSION; }
export function groupsFor(type: CounselorType): string[] { return type === 'career' ? GROUPS_CAREER : GROUPS_ADMISSION; }

export const CAREER_ONLY_ROUTES = ['/app/career'];
export const ADMISSION_ONLY_ROUTES = ['/app/applications', '/app/references'];

/** All nav items across both counselors — for the command palette / breadcrumbs. */
export const NAV: NavItem[] = (() => {
  const seen = new Set<string>();
  return [...NAV_ADMISSION, ...NAV_CAREER].filter(i => {
    const k = i.route + i.label;
    if (seen.has(k)) return false;
    seen.add(k); return true;
  });
})();
export const NAV_GROUPS = GROUPS_ADMISSION;
