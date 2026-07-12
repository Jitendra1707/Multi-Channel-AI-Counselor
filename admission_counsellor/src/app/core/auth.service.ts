import { Injectable, computed, effect, signal } from '@angular/core';
import { CurrentUser, Institution, Role } from '../domain/models';

const ROLE_LABEL: Record<Role, string> = {
  'super-admin': 'Super Admin',
  'institution-admin': 'Institution Admin',
  'admission-director': 'Admission Director',
  'admission-manager': 'Admission Manager',
  'ai-supervisor': 'AI Counselor Supervisor',
  'knowledge-manager': 'Knowledge Manager',
  'compliance-officer': 'Compliance & Approval Officer',
  'crm-manager': 'CRM / Data Manager',
  'human-counselor': 'Human Counselor',
  'management-viewer': 'University Management',
};

/** Role → landing route (§9.3) */
export const ROLE_HOME: Record<Role, string> = {
  'super-admin': '/app/overview',
  'institution-admin': '/app/overview',
  'admission-director': '/app/overview',
  'admission-manager': '/app/crm',
  'ai-supervisor': '/app/ai-counselor',
  'knowledge-manager': '/app/kms',
  'compliance-officer': '/app/approvals',
  'crm-manager': '/app/crm/import',
  'human-counselor': '/app/handoff',
  'management-viewer': '/app/analytics',
};

export interface TenantBranding {
  primary: string;
  primaryRgb: string;
  accent: string;
}
export interface TenantRecord {
  institutionId: string;
  name: string;
  shortName: string;
  type: string;
  domain: string;
  branding: TenantBranding;
  counselors: ('admission' | 'career')[];
}

/**
 * Tenant registry keyed by email domain. In production this is the platform
 * control-plane DB (domain_mapping → tenant + branding); the tenant is resolved
 * from the sign-in email domain and its branding hydrated into the session.
 */
export const TENANTS: Record<string, TenantRecord> = {
  'northgate.edu': { institutionId: 't-srinidhi', name: 'Srinidhi University', shortName: 'Srinidhi', type: 'University', domain: 'northgate.edu',
    branding: { primary: '#1E3A8A', primaryRgb: '30, 58, 138', accent: '#22D3EE' }, counselors: ['admission', 'career'] },
  'riverside.edu': { institutionId: 't-riverside', name: 'Riverside Institute of Technology', shortName: 'Riverside', type: 'Institute', domain: 'riverside.edu',
    branding: { primary: '#0F766E', primaryRgb: '15, 118, 110', accent: '#14B8A6' }, counselors: ['admission'] },
  'crestwood.edu': { institutionId: 't-crestwood', name: 'Crestwood College', shortName: 'Crestwood', type: 'College', domain: 'crestwood.edu',
    branding: { primary: '#7C3AED', primaryRgb: '124, 58, 237', accent: '#A78BFA' }, counselors: ['admission', 'career'] },
  'meridian.edu': { institutionId: 't-meridian', name: 'Meridian EdTech', shortName: 'Meridian', type: 'EdTech', domain: 'meridian.edu',
    branding: { primary: '#B45309', primaryRgb: '180, 83, 9', accent: '#F59E0B' }, counselors: ['career'] },
};

@Injectable({ providedIn: 'root' })
export class AuthService {
  readonly institution = signal<Institution>({
    institutionId: 't-srinidhi', name: 'Srinidhi University', shortName: 'Srinidhi', type: 'University',
  });
  readonly branding = signal<TenantBranding>(TENANTS['northgate.edu'].branding);
  readonly tenantDomain = signal<string>('northgate.edu');

  readonly user = signal<CurrentUser>(this.makeUser('admission-director', 'Priya Menon', 'priya.menon@northgate.edu'));
  readonly admissionCycle = signal<string>('Fall 2026');

  constructor() {
    // Apply the resolved tenant's branding into the live (secure) session.
    effect(() => {
      const b = this.branding();
      const root = document.documentElement;
      root.style.setProperty('--color-primary', b.primary);
      root.style.setProperty('--color-primary-rgb', b.primaryRgb);
    });
  }

  /** Resolve tenant from the sign-in email domain and hydrate branding. */
  resolveTenant(email: string): TenantRecord {
    const domain = (email.split('@')[1] || 'northgate.edu').toLowerCase().trim();
    const t = TENANTS[domain] ?? TENANTS['northgate.edu'];
    this.tenantDomain.set(t.domain);
    this.institution.set({ institutionId: t.institutionId, name: t.name, shortName: t.shortName, type: t.type });
    this.branding.set(t.branding);
    return t;
  }

  signInWithEmail(email: string, role: Role = 'admission-director') {
    this.resolveTenant(email);
    const name = email.split('@')[0].split('.').map(s => s.charAt(0).toUpperCase() + s.slice(1)).join(' ');
    this.user.set(this.makeUser(role, name || 'Tenant User', email));
  }

  signInAs(role: Role) {
    const presets: Partial<Record<Role, [string, string]>> = {
      'institution-admin': ['Anita Sharma', 'anita.sharma@northgate.edu'],
      'admission-director': ['Priya Menon', 'priya.menon@northgate.edu'],
      'admission-manager': ['Rahul Desai', 'rahul.desai@northgate.edu'],
      'knowledge-manager': ['Kavya Iyer', 'kavya.iyer@northgate.edu'],
      'compliance-officer': ['Sneha Banerjee', 'sneha.banerjee@northgate.edu'],
      'ai-supervisor': ['Imran Sheikh', 'imran.sheikh@northgate.edu'],
      'human-counselor': ['Meera Nair', 'meera.nair@northgate.edu'],
    };
    const [name, email] = presets[role] ?? ['Priya Menon', 'priya.menon@northgate.edu'];
    this.resolveTenant(email);
    this.user.set(this.makeUser(role, name, email));
  }

  private makeUser(role: Role, name: string, email: string): CurrentUser {
    const initials = name.split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase();
    return { userId: 'u-' + role, name, email, role, roleLabel: ROLE_LABEL[role], initials, hue: 222 };
  }
}
