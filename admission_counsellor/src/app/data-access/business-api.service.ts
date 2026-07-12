import { Injectable } from '@angular/core';
import { environment } from '../../environments/environment';
import { Candidate, CandidateStage } from '../domain/models';

/**
 * BusinessApiService — client for the BusinessLayer leads API (:8002). Plain
 * fetch, mirroring the proven web-app client (web-app/src/lib/businessApi.ts).
 * Used by the CRM screens to upload an Excel of leads, list them, and read a
 * lead's sessions.
 */

const BASE = environment.businessUrl.replace(/\/$/, '');

/** A document/item actually delivered to the lead (lead.sent_items). */
export interface SentItem {
  item: string;
  channel: string;
  at: string;
}

/** Lean list row (GET /leads). */
export interface LeadView {
  lead_id: string;
  full_name: string;
  email?: string | null;
  phone_e164: string;
  source: string;
  status: string;
  /** Admissions lifecycle stage (raw|lead|application_started|fees_pending|application_submitted). */
  funnel_stage?: string;
  /** Lead temperature (hot|warm|cold); null until analyzed. */
  lead_priority?: string | null;
  course_interest?: string | null;
  city?: string | null;
  interest?: number;
  updated_at?: string | null;
}

/** Full lead record (GET /leads/{id}) — the rolled-up CRM view. */
export interface LeadDetail extends LeadView {
  language_preference?: string;
  intake_year?: number | null;
  parent_name?: string | null;
  parent_phone_e164?: string | null;
  consent_call?: boolean;
  consent_whatsapp?: boolean;
  facts?: Record<string, unknown>;
  confidence?: number;
  summary?: string | null;
  open_concerns?: string[];
  sent_items?: SentItem[];
  call_attempts?: number;
  next_action_at?: string | null;
  last_whatsapp_inbound_at?: string | null;
}

export interface LeadSession {
  session_id: string;
  channel: string;
  direction: string;
  status: string;
  end_reason?: string | null;
  analyzed: boolean;
  turns: number;
  transcript: Array<{ role?: string; text?: string; ts?: number }>;
  analysis?: Record<string, unknown> | null;
  started_at?: string | null;
  ended_at?: string | null;
}

export interface UploadLeadsResult {
  inserted: number;
  duplicates: number;
  errors: number;
  rows: number;
  error_details: string[];
  inserted_ids: string[];
}

export interface ResetResult {
  ok: boolean;
  cleared: Record<string, number>;
}

/**
 * BusinessLayer status → display label. The backend status is shown verbatim —
 * only polished for the view (snake_case → Title Case), never remapped to a
 * different vocabulary. Statuses not listed here are still shown verbatim via
 * `prettifyStatus` (never masked as "New"); only a blank status falls back to
 * "New". New leads land as "New" straight after import.
 */
const STATUS_LABEL: Record<string, CandidateStage> = {
  new: 'New',
  welcomed: 'Welcomed',
  scheduling: 'Scheduling',
  scheduled: 'Scheduled',
  in_call: 'In Call',
  called: 'Called',
  followup: 'Follow-up',
  escalated: 'Escalated',
  delegated: 'Delegated',
  not_interested: 'Not Interested',
  converted: 'Converted',
  lost: 'Lost',
  closed: 'Closed',
};

/** Polish any backend status for display (snake_case → Title Case); '' → 'New'. */
function prettifyStatus(s: string): CandidateStage {
  if (!s) return 'New';
  return s.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ') as CandidateStage;
}

/** BusinessLayer lead source → a human "created via" label. */
const SOURCE_LABEL: Record<string, string> = {
  upload: 'Excel Import', api: 'API', inbound_voice: 'Inbound call', inbound_whatsapp: 'Inbound WhatsApp',
};

function hueFromId(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 360;
  return h;
}

/**
 * Map a backend lead → the rich app Candidate. Group-A fields (analyzer-
 * generated) are taken from the lead; Group-B fields (no backend source) get
 * neutral defaults and `backed: true` so the UI renders "—" for them.
 */
export function leadToCandidate(lead: LeadView | LeadDetail): Candidate {
  const d = lead as LeadDetail;
  return {
    candidateId: lead.lead_id,
    name: lead.full_name || 'Unknown',
    avatarHue: hueFromId(lead.lead_id),
    mobile: lead.phone_e164,
    whatsapp: lead.phone_e164,
    email: lead.email ?? '',
    city: lead.city ?? '',
    region: '',
    country: 'India',
    academicBackground: '',
    careerInterests: [],
    preferredCourse: lead.course_interest ?? '',
    budgetRange: '',
    budgetSensitivity: 'low',
    scholarshipInterest: false,
    parents: d.parent_name
      ? [{
          parentId: 'par-' + lead.lead_id, candidateId: lead.lead_id, name: d.parent_name,
          relationship: 'Guardian', mobile: d.parent_phone_e164 ?? undefined,
          preferredLanguage: d.language_preference ?? 'English', concerns: [],
          sentiment: 'neutral', consentToDiscuss: true,
        }]
      : [],
    consent: {
      call: d.consent_call ?? true, whatsapp: d.consent_whatsapp ?? true,
      email: true, recording: false, source: lead.source,
    },
    leadSource: lead.source,
    currentStage: STATUS_LABEL[lead.status] ?? prettifyStatus(lead.status),
    funnelStage: lead.funnel_stage ?? '',
    leadPriority: lead.lead_priority ?? null,
    conversionProbability: lead.interest ?? d.interest ?? 0,
    dropOffRisk: 'low',   // not computed for real leads; neutral default (not shown on backed screens)
    sentiment: 'neutral',
    assignedAiCounselor: 'Aisha',
    parentEngagement: 'None',
    applicationStatus: '—',
    tags: [],
    lastContacted: lead.updated_at ?? '',
    nextFollowUp: d.next_action_at ?? undefined,
    createdBy: SOURCE_LABEL[lead.source] ?? (lead.source || 'System'),
    createdAt: lead.updated_at ?? '',
    pendingQuestions: d.open_concerns ?? [],
    sentItems: d.sent_items ?? [],
    lastAiSummary: d.summary ?? '',
    recommendedNextAction: { label: '', channel: 'note', reason: '' },
    backed: true,
  };
}

@Injectable({ providedIn: 'root' })
export class BusinessApiService {
  /** Upload an Excel (.xlsx/.xlsm) of leads → parsed + inserted by BusinessLayer. */
  async uploadLeads(file: File): Promise<UploadLeadsResult> {
    const form = new FormData();
    form.append('file', file);
    let res: Response;
    try {
      res = await fetch(`${BASE}/leads/upload`, { method: 'POST', body: form });
    } catch {
      throw new Error('LEADS_SERVICE_UNAVAILABLE');
    }
    if (!res.ok) {
      if (res.status === 404) throw new Error('LEADS_SERVICE_UNAVAILABLE');
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json())?.detail ?? detail; } catch { /* ignore */ }
      throw new Error(detail);
    }
    return res.json() as Promise<UploadLeadsResult>;
  }

  /** List leads (most-recently-updated first). Optional status filter. */
  async listLeads(opts?: { status?: string; limit?: number }): Promise<LeadView[]> {
    const params = new URLSearchParams();
    if (opts?.status) params.set('status', opts.status);
    params.set('limit', String(opts?.limit ?? 200));
    let res: Response;
    try {
      res = await fetch(`${BASE}/leads?${params.toString()}`);
    } catch {
      throw new Error('LEADS_SERVICE_UNAVAILABLE');
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json() as Promise<LeadView[]>;
  }

  /** Full lead detail (rolled-up facts/interest/summary/concerns/meta). */
  async getLead(id: string): Promise<LeadDetail> {
    let res: Response;
    try {
      res = await fetch(`${BASE}/leads/${encodeURIComponent(id)}`);
    } catch {
      throw new Error('LEADS_SERVICE_UNAVAILABLE');
    }
    if (res.status === 404) throw new Error('LEAD_NOT_FOUND');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json() as Promise<LeadDetail>;
  }

  /** Schedule a follow-up: sets status→followup and next_action_at (default +24h). */
  async scheduleFollowup(id: string, inMinutes = 1440): Promise<void> {
    let res: Response;
    try {
      res = await fetch(`${BASE}/leads/${encodeURIComponent(id)}/schedule-followup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ in_minutes: inMinutes }),
      });
    } catch {
      throw new Error('LEADS_SERVICE_UNAVAILABLE');
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  }

  /** All sessions for a lead (most-recent first), each with its analysis. */
  async listLeadSessions(id: string): Promise<LeadSession[]> {
    let res: Response;
    try {
      res = await fetch(`${BASE}/leads/${encodeURIComponent(id)}/sessions`);
    } catch {
      throw new Error('LEADS_SERVICE_UNAVAILABLE');
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json() as Promise<LeadSession[]>;
  }

  /** DESTRUCTIVE: clear ALL leads, sessions and tasks (tables kept). */
  async resetLeads(): Promise<ResetResult> {
    let res: Response;
    try {
      res = await fetch(`${BASE}/leads/reset`, { method: 'POST' });
    } catch {
      throw new Error('LEADS_SERVICE_UNAVAILABLE');
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json() as Promise<ResetResult>;
  }
}
