import { computed, Injectable, inject, signal } from '@angular/core';
import {
  Candidate, CommEvent, KmsDoc, ApprovalRequest, Escalation, Application,
  ReferenceProvider, Metric, FunnelStage, BarDatum, InsightCard, ActivityItem, AppNotification,
} from '../domain/models';
import * as seed from './seed';
import { BusinessApiService, leadToCandidate } from './business-api.service';
import { ToastService } from '../core/toast.service';

/**
 * Central in-memory store for the prototype. In production this would be a set
 * of NgRx feature stores fed by the LiveGateway + REST services (§38). Here we
 * expose plain signals so components stay reactive and simple.
 */
@Injectable({ providedIn: 'root' })
export class DataStore {
  readonly candidates = signal<Candidate[]>(seed.buildCandidates());
  readonly kmsDocs = signal<KmsDoc[]>(seed.buildKms());
  readonly approvals = signal<ApprovalRequest[]>(seed.buildApprovals());
  readonly applications = signal<Application[]>([]);
  readonly escalations = signal<Escalation[]>([]);
  readonly references = signal<ReferenceProvider[]>(seed.buildReferences());
  readonly metrics = signal<Metric[]>(seed.buildMetrics());
  readonly funnel = signal<FunnelStage[]>(seed.buildFunnel());
  readonly leadSources = signal<BarDatum[]>(seed.buildLeadSources());
  readonly courseDemand = signal<BarDatum[]>(seed.buildCourseDemand());
  readonly probabilityDist = signal<BarDatum[]>(seed.buildProbabilityDist());
  readonly insights = signal<InsightCard[]>(seed.buildInsights());
  readonly activity = signal<ActivityItem[]>([]);
  readonly notifications = signal<AppNotification[]>(seed.buildNotifications());

  // ---- Career Counselor (Vera) data ----
  readonly careerMetrics = signal<Metric[]>(seed.buildCareerMetrics());
  readonly careerFunnel = signal<FunnelStage[]>(seed.buildCareerFunnel());
  readonly careerInterests = signal<BarDatum[]>(seed.buildCareerInterests());
  readonly careerReadiness = signal<BarDatum[]>(seed.buildCareerReadiness());
  readonly careerInsights = signal<InsightCard[]>(seed.buildCareerInsights());

  readonly unreadCount = computed(() => this.notifications().filter(n => !n.read).length);

  // ── Real leads (BusinessLayer) ──
  // Kept SEPARATE from the seeded `candidates` (which still backs the other,
  // not-yet-integrated prototype screens). The CRM Leads page reads `leads`
  // only — real data, no mock fallback.
  private api = inject(BusinessApiService);
  private toast = inject(ToastService);
  readonly leads = signal<Candidate[]>([]);
  readonly leadsLoading = signal(false);
  readonly leadsLoaded = signal(false);
  readonly leadsError = signal<string | null>(null);

  /**
   * Load real leads from BusinessLayer into `leads`. On failure, `leads` stays
   * empty and `leadsError` is set (the CRM page shows an error state — never the
   * mock seed). Safe to call repeatedly (e.g. after an import).
   *
   * `silent` is for the CRM auto-refresh poll: it updates the rows WITHOUT
   * toggling the loading spinner, and a transient failure does NOT blank the
   * table or flip the error state (we keep the last-good rows on screen).
   */
  async loadLeads(opts?: { silent?: boolean }): Promise<void> {
    const silent = opts?.silent === true;
    if (!silent) {
      this.leadsLoading.set(true);
      this.leadsError.set(null);
    }
    try {
      const rows = await this.api.listLeads({ limit: 200 });
      this.leads.set(rows.map(leadToCandidate));
      this.leadsLoaded.set(true);
      if (silent) this.leadsError.set(null); // a recovered poll clears a prior error
    } catch (e) {
      if (!silent) {
        this.leads.set([]);
        this.leadsError.set(e instanceof Error ? e.message : 'Failed to load leads');
      }
      // silent poll failure: keep the existing rows, try again next tick.
    } finally {
      if (!silent) this.leadsLoading.set(false);
    }
  }

  leadById(id: string): Candidate | undefined {
    return this.leads().find(c => c.candidateId === id);
  }

  constructor() {
    const cands = this.candidates();
    this.applications.set(seed.buildApplications(cands));
    this.escalations.set(seed.buildEscalations(cands));
    this.activity.set(seed.buildActivity(cands));
  }

  candidateById(id: string): Candidate | undefined {
    return this.candidates().find(c => c.candidateId === id);
  }
  journeyFor(id: string) { const c = this.candidateById(id); return c ? seed.buildJourney(c) : []; }
  chatFor(id: string) { const c = this.candidateById(id); return c ? seed.buildChat(c) : []; }

  updateCandidate(id: string, patch: Partial<Candidate>) {
    this.candidates.update(list => list.map(c => c.candidateId === id ? { ...c, ...patch } : c));
  }

  markAllRead() { this.notifications.update(n => n.map(x => ({ ...x, read: true }))); }
  markRead(id: string) { this.notifications.update(n => n.map(x => x.id === id ? { ...x, read: true } : x)); }

  claimEscalation(id: string, who: string) {
    this.escalations.update(list => list.map(e => e.escalationId === id ? { ...e, status: 'Claimed', assignedTo: who } : e));
  }
  resolveEscalation(id: string) {
    this.escalations.update(list => list.map(e => e.escalationId === id ? { ...e, status: 'Resolved' } : e));
  }

  approve(id: string) { this.approvals.update(l => l.filter(a => a.requestId !== id)); }
  reject(id: string) { this.approvals.update(l => l.filter(a => a.requestId !== id)); }
}
