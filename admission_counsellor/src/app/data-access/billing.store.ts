import { Injectable, computed, signal } from '@angular/core';
import {
  Plan, PlanTier, PLAN_BY_TIER, TENANT_TIER, TENANT_DISCOUNT, TAX, RENEWAL_DATE, CONTRACT_START,
  Invoice, Payment, CreditTxn, UsageAlert, RateLimitPolicy, AlertChannel, MetricKey,
  seedInvoices, seedPayments, seedCredits, seedAlerts, seedRateLimits,
  allMeterRows, cycleOverageINR, applyTax, MONTHLY_USAGE, CYCLE_LABEL,
} from './billing.data';

export interface AutoRecharge { enabled: boolean; thresholdINR: number; topupINR: number; }

/**
 * Tenant commercial store — the institution admin's self-service view of THEIR
 * plan, metering, invoices, credits, alerts and rate-limit thresholds. Actions
 * (pay invoice, top up credits, edit alerts/limits) mutate local signals; in
 * production these are REST calls to the control plane scoped by tenant + JWT.
 */
@Injectable({ providedIn: 'root' })
export class BillingStore {
  readonly tier = signal<PlanTier>(TENANT_TIER);
  readonly invoices = signal<Invoice[]>(seedInvoices());
  readonly payments = signal<Payment[]>(seedPayments());
  readonly credits = signal<CreditTxn[]>(seedCredits());
  readonly alerts = signal<UsageAlert[]>(seedAlerts());
  readonly rateLimits = signal<RateLimitPolicy[]>(seedRateLimits());
  readonly autoRecharge = signal<AutoRecharge>({ enabled: true, thresholdINR: 25000, topupINR: 50000 });

  readonly tax = TAX;
  readonly discount = TENANT_DISCOUNT;
  readonly renewalDate = RENEWAL_DATE;
  readonly contractStart = CONTRACT_START;
  readonly cycleLabel = CYCLE_LABEL;

  private seq = signal(100);

  readonly plan = computed<Plan>(() => PLAN_BY_TIER[this.tier()]);

  /** Effective annual licence after the negotiated discount (pre-GST). */
  readonly effectiveAnnualINR = computed(() => {
    const base = this.plan().annualPriceINR;
    return base - Math.round(base * (this.discount.pct / 100));
  });
  readonly meterRows = computed(() => allMeterRows(this.plan()));
  readonly cycleOverageINR = computed(() => cycleOverageINR(this.plan()));
  readonly overMetrics = computed(() => this.meterRows().filter(r => r.over > 0));
  readonly nearLimit = computed(() => this.meterRows().filter(r => r.loadPct >= 80 && r.over === 0));

  /** Current-cycle estimated bill (overage + GST), what the credits cushion covers. */
  readonly cycleEstimate = computed(() => applyTax(
    this.cycleOverageINR() - Math.round(this.cycleOverageINR() * (this.discount.pct / 100)), this.tax));

  readonly creditBalance = computed(() => this.credits().reduce((a, c) => a + c.amountINR, 0));
  readonly creditCoverageDays = computed(() => {
    const daily = Math.max(1, Math.round(this.cycleOverageINR() / 30));
    return Math.round(this.creditBalance() / daily);
  });

  readonly outstanding = computed(() => this.invoices().filter(i => i.status === 'due' || i.status === 'overdue'));
  readonly outstandingINR = computed(() => this.outstanding().reduce((a, i) => a + i.totalINR, 0));
  readonly paidThisFY = computed(() => this.payments().reduce((a, p) => a + p.amountINR, 0));
  readonly nextDue = computed(() => this.outstanding().slice().sort((a, b) => a.due.localeCompare(b.due))[0]);

  invoiceById(id: string) { return this.invoices().find(i => i.id === id); }

  // ---- actions ----
  private nextId(prefix: string) { this.seq.update(n => n + 1); return `${prefix}-${this.seq()}`; }

  payInvoice(id: string, method: string, when: string) {
    const inv = this.invoiceById(id);
    if (!inv) return;
    this.invoices.update(list => list.map(i => i.id === id ? { ...i, status: 'paid' as const, paidOn: when, method } : i));
    this.payments.update(list => [
      { id: this.nextId('PAY'), date: when, amountINR: inv.totalINR, method, reference: 'pay_' + this.nextId('rzp').replace(/[^0-9]/g, ''), label: inv.period + ' invoice' },
      ...list,
    ]);
  }

  loadCredits(amountINR: number, method: string, when: string) {
    this.credits.update(list => [
      { id: this.nextId('CR'), date: when, kind: 'topup', label: `Credit top-up — ${method.split('·').pop()?.trim() || method}`, amountINR },
      ...list,
    ]);
    this.payments.update(list => [
      { id: this.nextId('PAY'), date: when, amountINR, method, reference: 'pay_' + this.nextId('rzp').replace(/[^0-9]/g, ''), label: 'Credit top-up' },
      ...list,
    ]);
  }

  setAutoRecharge(patch: Partial<AutoRecharge>) { this.autoRecharge.update(a => ({ ...a, ...patch })); }

  addAlert(metric: MetricKey, thresholdPct: number, channels: AlertChannel[]) {
    this.alerts.update(list => [...list, { id: this.nextId('AL'), metric, thresholdPct, channels, enabled: true }]);
  }
  updateAlert(id: string, patch: Partial<UsageAlert>) {
    this.alerts.update(list => list.map(a => a.id === id ? { ...a, ...patch } : a));
  }
  toggleAlert(id: string) { this.alerts.update(list => list.map(a => a.id === id ? { ...a, enabled: !a.enabled } : a)); }
  removeAlert(id: string) { this.alerts.update(list => list.filter(a => a.id !== id)); }

  updateRateLimit(id: string, perMinute: number) {
    this.rateLimits.update(list => list.map(r => r.id === id ? { ...r, perMinute } : r));
  }

  setTier(tier: PlanTier) { this.tier.set(tier); }
}
