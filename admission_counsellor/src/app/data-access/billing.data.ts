/* =====================================================================
   Tenant-side commercial model — what the institution's admin sees and
   manages for THEIR OWN tenant: plan & entitlements, granular metering,
   overage, invoices + GST, payments, prepaid credits and threshold alerts.

   This mirrors the platform-admin commercial model (the control plane owns
   the master record) but is scoped to the single signed-in tenant and adds
   self-service: pay an invoice, top up credits, configure usage alerts and
   rate-limit thresholds. Money is INR; metered usage is shown per monthly
   billing cycle (annual allowances divided into a monthly run-rate).
   ===================================================================== */

export type MetricKey =
  | 'voice.calls' | 'voice.pulses' | 'msg.whatsapp' | 'msg.sms' | 'email.sent'
  | 'vcon.sessions' | 'vcon.minutes' | 'meetings' | 'ai.interactions'
  | 'seats' | 'kms.docs' | 'storage.gb' | 'api.calls';

export interface MeterDef { key: MetricKey; label: string; short: string; unit: string; icon: string; block: number; hint?: string; }
export interface PlanQuota { metric: MetricKey; included: number; overageINR: number; }
export interface FeatureFlag { key: string; label: string; included: boolean; }
export type PlanTier = 'startup' | 'growth' | 'enterprise';
export interface Plan {
  id: string; tier: PlanTier; name: string; tagline: string;
  annualPriceINR: number; quotas: PlanQuota[]; featureMatrix: FeatureFlag[]; popular?: boolean;
}
export interface TaxConfig {
  gstPercent: number; cgstPercent: number; sgstPercent: number; igstPercent: number;
  intraState: boolean; placeOfSupply: string; gstin: string; legalName: string;
}
export type InvoiceStatus = 'paid' | 'due' | 'overdue' | 'draft';
export interface InvoiceLine { kind: 'base' | 'overage' | 'credit'; label: string; qty: number; unitINR: number; amountINR: number; }
export interface Invoice {
  id: string; period: string; kind: 'licence' | 'usage';
  issued: string; due: string; status: InvoiceStatus; paidOn?: string; method?: string;
  lines: InvoiceLine[];
  subtotalINR: number; discountPercent: number; discountINR: number; taxableINR: number;
  cgstINR: number; sgstINR: number; igstINR: number; taxINR: number; totalINR: number;
}
export interface Payment { id: string; date: string; amountINR: number; method: string; reference: string; label: string; }
export interface CreditTxn { id: string; date: string; kind: 'topup' | 'consume' | 'bonus'; label: string; amountINR: number; }
export type AlertChannel = 'inapp' | 'email' | 'sms';
export interface UsageAlert { id: string; metric: MetricKey; thresholdPct: number; channels: AlertChannel[]; enabled: boolean; }
export interface RateLimitPolicy { id: string; scope: string; label: string; perMinute: number; current: number; burst: number; }

/* ---------- metric catalog ---------- */
export const METRICS: MeterDef[] = [
  { key: 'voice.calls', label: 'AI voice calls', short: 'Calls', unit: 'calls', icon: 'phone', block: 1 },
  { key: 'voice.pulses', label: 'Telephony pulses (60s)', short: 'Pulses', unit: 'pulses', icon: 'activity', block: 1, hint: '1 pulse = 60s of connected call time' },
  { key: 'msg.whatsapp', label: 'WhatsApp messages', short: 'WhatsApp', unit: 'msgs', icon: 'message-circle', block: 1 },
  { key: 'msg.sms', label: 'SMS messages', short: 'SMS', unit: 'msgs', icon: 'message-square', block: 1 },
  { key: 'email.sent', label: 'Emails sent', short: 'Email', unit: 'emails', icon: 'mail', block: 1 },
  { key: 'vcon.sessions', label: 'Video consultations (V-Cons)', short: 'V-Cons', unit: 'sessions', icon: 'video', block: 1 },
  { key: 'vcon.minutes', label: 'Video minutes', short: 'Video min', unit: 'min', icon: 'clock', block: 1 },
  { key: 'meetings', label: 'Meetings hosted', short: 'Meetings', unit: 'meetings', icon: 'calendar', block: 1 },
  { key: 'ai.interactions', label: 'AI counselling interactions', short: 'AI msgs', unit: 'interactions', icon: 'sparkles', block: 100, hint: 'Charged per 100 AI interactions' },
  { key: 'seats', label: 'Active user seats', short: 'Seats', unit: 'seats', icon: 'users', block: 1 },
  { key: 'kms.docs', label: 'Knowledge documents', short: 'KMS docs', unit: 'docs', icon: 'book-open', block: 1 },
  { key: 'storage.gb', label: 'Storage', short: 'Storage', unit: 'GB', icon: 'database', block: 1 },
  { key: 'api.calls', label: 'API calls', short: 'API', unit: 'calls', icon: 'plug', block: 100000, hint: 'Charged per 100k API calls' },
];
export const METRIC_BY_KEY: Record<MetricKey, MeterDef> = Object.fromEntries(METRICS.map(m => [m.key, m])) as Record<MetricKey, MeterDef>;

const q = (metric: MetricKey, included: number, overageINR: number): PlanQuota => ({ metric, included, overageINR });
const FEATURES = (career: boolean, vcon: boolean, sso: boolean, vpc: boolean, guardrails: boolean, presentations: boolean, support: string): FeatureFlag[] => [
  { key: 'admission', label: 'Aisha — Admission counsellor', included: true },
  { key: 'career', label: 'Vera — Career counsellor', included: career },
  { key: 'voice', label: 'Voice + WhatsApp + Email channels', included: true },
  { key: 'vcon', label: 'V-Cons video consultations', included: vcon },
  { key: 'meetings', label: 'Collaborative meetings & calendar', included: true },
  { key: 'crm', label: 'CRM / Students + profiles', included: true },
  { key: 'kms', label: 'Knowledge base (KMS) + approvals', included: true },
  { key: 'analytics', label: 'Analytics & conversion intelligence', included: true },
  { key: 'presentations', label: 'AI-narrated management reports', included: presentations },
  { key: 'sso', label: 'SSO (SAML / Google / Entra)', included: sso },
  { key: 'vpc', label: 'Dedicated region & VPC peering', included: vpc },
  { key: 'guardrails', label: 'Custom guardrail policies', included: guardrails },
  { key: 'support', label: support, included: true },
];

/* ---------- the three priced tiers (annual licence fee, INR) ---------- */
export const PLANS: Plan[] = [
  {
    id: 'plan-startup', tier: 'startup', name: 'Startup',
    tagline: 'For single-campus colleges starting with one AI counsellor.',
    annualPriceINR: 2500000,
    quotas: [
      q('voice.calls', 50000, 3), q('voice.pulses', 300000, 0.5), q('msg.whatsapp', 200000, 0.35),
      q('msg.sms', 100000, 0.18), q('email.sent', 500000, 0.05), q('vcon.sessions', 2000, 40),
      q('vcon.minutes', 60000, 2.5), q('meetings', 5000, 15), q('ai.interactions', 1000000, 0.4),
      q('seats', 25, 6000), q('kms.docs', 1000, 50), q('storage.gb', 250, 120), q('api.calls', 5000000, 400),
    ],
    featureMatrix: FEATURES(false, false, false, false, false, false, 'Email support (next business day)'),
  },
  {
    id: 'plan-growth', tier: 'growth', name: 'Growth', popular: true,
    tagline: 'For colleges & institutes scaling admissions and career counselling.',
    annualPriceINR: 5000000,
    quotas: [
      q('voice.calls', 150000, 2.5), q('voice.pulses', 1000000, 0.4), q('msg.whatsapp', 600000, 0.3),
      q('msg.sms', 300000, 0.15), q('email.sent', 1500000, 0.04), q('vcon.sessions', 6000, 35),
      q('vcon.minutes', 200000, 2), q('meetings', 15000, 12), q('ai.interactions', 3500000, 0.35),
      q('seats', 75, 5500), q('kms.docs', 3500, 40), q('storage.gb', 750, 100), q('api.calls', 18000000, 350),
    ],
    featureMatrix: FEATURES(true, true, true, false, false, true, 'Priority support (8×5) + onboarding'),
  },
  {
    id: 'plan-enterprise', tier: 'enterprise', name: 'Enterprise',
    tagline: 'For universities & groups with multi-campus, compliance & scale needs.',
    annualPriceINR: 7500000,
    quotas: [
      q('voice.calls', 400000, 2), q('voice.pulses', 3000000, 0.3), q('msg.whatsapp', 1500000, 0.25),
      q('msg.sms', 800000, 0.12), q('email.sent', 4000000, 0.03), q('vcon.sessions', 18000, 30),
      q('vcon.minutes', 600000, 1.75), q('meetings', 40000, 10), q('ai.interactions', 10000000, 0.3),
      q('seats', 200, 5000), q('kms.docs', 10000, 30), q('storage.gb', 2000, 85), q('api.calls', 50000000, 300),
    ],
    featureMatrix: FEATURES(true, true, true, true, true, true, '24×7 priority support + dedicated TAM'),
  },
];
export const PLAN_BY_TIER: Record<PlanTier, Plan> = Object.fromEntries(PLANS.map(p => [p.tier, p])) as Record<PlanTier, Plan>;

/* ---------- this tenant (Northgate University) commercial profile ---------- */
export const TENANT_TIER: PlanTier = 'enterprise';
export const TENANT_DISCOUNT = { pct: 12, reason: 'Multi-year + flagship reference account' };
export const RENEWAL_DATE = '2026-08-12';
export const CONTRACT_START = '2025-08-12';

export const TAX: TaxConfig = {
  gstPercent: 18, cgstPercent: 9, sgstPercent: 9, igstPercent: 18,
  intraState: true, placeOfSupply: 'Karnataka (29)',
  gstin: '29ABCDE1234F1Z5', legalName: 'Admission Counsellor Technologies Pvt. Ltd.',
};

/* ---------- this month's metered usage (the live billing cycle) ----------
   Allowance shown is the monthly run-rate = annual included / 12. A few
   metrics deliberately run over to demonstrate overage + credit drawdown. */
export const CYCLE_LABEL = 'Jun 2026';
export const MONTHLY_USAGE: Record<MetricKey, number> = {
  'voice.calls': 24800,        // allow ~33,333
  'voice.pulses': 268000,      // allow 250,000 → OVER
  'msg.whatsapp': 142000,      // allow 125,000 → OVER
  'msg.sms': 41000,            // allow ~66,666
  'email.sent': 295000,        // allow ~333,333
  'vcon.sessions': 1180,       // allow 1,500
  'vcon.minutes': 54200,       // allow 50,000 → OVER
  'meetings': 2640,            // allow ~3,333
  'ai.interactions': 902000,   // allow ~833,333 → OVER
  'seats': 94,                 // allow 200
  'kms.docs': 412,             // allow ~833
  'storage.gb': 165,           // allow ~166
  'api.calls': 3120000,        // allow ~4,166,666
};

/** Monthly allowance for a metric = annual included / 12 (rounded). */
export function monthlyAllowance(plan: Plan, metric: MetricKey): number {
  const qz = plan.quotas.find(x => x.metric === metric);
  if (!qz) return 0;
  // seats / storage / kms are standing allowances, not monthly-consumed → keep as-is.
  if (metric === 'seats' || metric === 'storage.gb' || metric === 'kms.docs') return qz.included;
  return Math.round(qz.included / 12);
}

export interface MeterRow {
  key: MetricKey; def: MeterDef; used: number; allowance: number;
  over: number; overINR: number; loadPct: number; rate: number;
}

/** Compute a per-metric meter row for the current cycle against a plan. */
export function meterRow(plan: Plan, metric: MetricKey, usage = MONTHLY_USAGE): MeterRow {
  const def = METRIC_BY_KEY[metric];
  const qz = plan.quotas.find(x => x.metric === metric);
  const allowance = monthlyAllowance(plan, metric);
  const used = usage[metric] ?? 0;
  const over = Math.max(0, used - allowance);
  const rate = qz?.overageINR ?? 0;
  const overINR = over > 0 ? Math.round((over / def.block) * rate) : 0;
  const loadPct = allowance > 0 ? Math.min(160, Math.round((used / allowance) * 100)) : 0;
  return { key: metric, def, used, allowance, over, overINR, loadPct, rate };
}

export function allMeterRows(plan: Plan, usage = MONTHLY_USAGE): MeterRow[] {
  return METRICS.map(m => meterRow(plan, m.key, usage));
}

/** Total accrued overage (₹, pre-tax) for the current cycle. */
export function cycleOverageINR(plan: Plan, usage = MONTHLY_USAGE): number {
  return allMeterRows(plan, usage).reduce((a, r) => a + r.overINR, 0);
}

/* ---------- GST helper ---------- */
export interface TaxBreakup { taxableINR: number; cgstINR: number; sgstINR: number; igstINR: number; taxINR: number; totalINR: number; }
export function applyTax(taxableINR: number, tax: TaxConfig): TaxBreakup {
  let cgstINR = 0, sgstINR = 0, igstINR = 0;
  if (tax.intraState) {
    cgstINR = Math.round(taxableINR * (tax.cgstPercent / 100));
    sgstINR = Math.round(taxableINR * (tax.sgstPercent / 100));
  } else {
    igstINR = Math.round(taxableINR * (tax.igstPercent / 100));
  }
  const taxINR = cgstINR + sgstINR + igstINR;
  return { taxableINR, cgstINR, sgstINR, igstINR, taxINR, totalINR: taxableINR + taxINR };
}

/** Build an invoice from raw lines, applying discount then GST. */
export function buildInvoice(
  base: Pick<Invoice, 'id' | 'period' | 'kind' | 'issued' | 'due' | 'status' | 'paidOn' | 'method'>,
  lines: InvoiceLine[], discountPercent: number, tax: TaxConfig,
): Invoice {
  const subtotalINR = lines.reduce((a, l) => a + l.amountINR, 0);
  const discountINR = Math.round(subtotalINR * (discountPercent / 100));
  const t = applyTax(subtotalINR - discountINR, tax);
  return {
    ...base, lines, subtotalINR, discountPercent, discountINR,
    taxableINR: t.taxableINR, cgstINR: t.cgstINR, sgstINR: t.sgstINR, igstINR: t.igstINR,
    taxINR: t.taxINR, totalINR: t.totalINR,
  };
}

/* ---------- seed invoices, payments, credits, alerts, rate limits ---------- */
const ENT = PLAN_BY_TIER[TENANT_TIER];

function usageInvoice(id: string, period: string, issued: string, due: string, status: InvoiceStatus, paidOn: string | undefined, overINR: number, lines: InvoiceLine[]): Invoice {
  return buildInvoice({ id, period, kind: 'usage', issued, due, status, paidOn, method: paidOn ? 'Razorpay · UPI' : undefined }, lines, TENANT_DISCOUNT.pct, TAX);
}

export function seedInvoices(): Invoice[] {
  return [
    // Annual platform licence (paid at contract start)
    buildInvoice(
      { id: 'INV-NORTH-2026-LIC', period: 'FY 2025–26 licence', kind: 'licence', issued: '2025-08-12', due: '2025-08-26', status: 'paid', paidOn: '2025-08-14', method: 'NEFT · HDFC' },
      [{ kind: 'base', label: 'Enterprise platform licence — annual', qty: 1, unitINR: ENT.annualPriceINR, amountINR: ENT.annualPriceINR }],
      TENANT_DISCOUNT.pct, TAX,
    ),
    usageInvoice('INV-NORTH-202604', 'Apr 2026 usage', '2026-05-01', '2026-05-15', 'paid', '2026-05-09', 0, [
      { kind: 'overage', label: 'Overage — WhatsApp messages (12,400 over)', qty: 12400, unitINR: 0.25, amountINR: 3100 },
      { kind: 'overage', label: 'Overage — AI interactions (41,000 over)', qty: 41000, unitINR: 0.30 / 100, amountINR: 123 },
    ]),
    usageInvoice('INV-NORTH-202605', 'May 2026 usage', '2026-06-01', '2026-06-15', 'paid', '2026-06-04', 0, [
      { kind: 'overage', label: 'Overage — Telephony pulses (14,200 over)', qty: 14200, unitINR: 0.30, amountINR: 4260 },
      { kind: 'overage', label: 'Overage — WhatsApp messages (15,800 over)', qty: 15800, unitINR: 0.25, amountINR: 3950 },
      { kind: 'overage', label: 'Overage — Video minutes (3,100 over)', qty: 3100, unitINR: 1.75, amountINR: 5425 },
    ]),
    // Current cycle — DUE now (the one the admin should pay on time)
    usageInvoice('INV-NORTH-202606', 'Jun 2026 usage', '2026-06-15', '2026-06-29', 'due', undefined, 0, [
      { kind: 'overage', label: 'Overage — Telephony pulses (18,000 over)', qty: 18000, unitINR: 0.30, amountINR: 5400 },
      { kind: 'overage', label: 'Overage — WhatsApp messages (17,000 over)', qty: 17000, unitINR: 0.25, amountINR: 4250 },
      { kind: 'overage', label: 'Overage — Video minutes (4,200 over)', qty: 4200, unitINR: 1.75, amountINR: 7350 },
      { kind: 'overage', label: 'Overage — AI interactions (68,667 over)', qty: 68667, unitINR: 0.30 / 100, amountINR: 206 },
    ]),
  ];
}

export function seedPayments(): Payment[] {
  return [
    { id: 'PAY-0007', date: '2026-06-04', amountINR: 15399, method: 'Razorpay · UPI', reference: 'pay_NkQ8s2May', label: 'May 2026 usage invoice' },
    { id: 'PAY-0006', date: '2026-05-20', amountINR: 50000, method: 'Razorpay · Card', reference: 'pay_NkA1credit', label: 'Credit top-up' },
    { id: 'PAY-0005', date: '2026-05-09', amountINR: 3593, method: 'Razorpay · UPI', reference: 'pay_NjB7s2Apr', label: 'Apr 2026 usage invoice' },
    { id: 'PAY-0004', date: '2025-08-14', amountINR: 7788000, method: 'NEFT · HDFC', reference: 'UTR2025081400182', label: 'FY 2025–26 platform licence' },
  ];
}

export function seedCredits(): CreditTxn[] {
  return [
    { id: 'CR-09', date: '2026-06-10', kind: 'consume', label: 'Auto-applied to Jun overage (pulses)', amountINR: -3200 },
    { id: 'CR-08', date: '2026-05-20', kind: 'topup', label: 'Credit top-up — Card', amountINR: 50000 },
    { id: 'CR-07', date: '2026-05-12', kind: 'consume', label: 'Auto-applied to May overage (video)', amountINR: -5425 },
    { id: 'CR-06', date: '2026-04-02', kind: 'bonus', label: 'Annual renewal bonus credits', amountINR: 25000 },
    { id: 'CR-05', date: '2026-03-18', kind: 'topup', label: 'Credit top-up — UPI', amountINR: 60000 },
  ];
}

export function seedAlerts(): UsageAlert[] {
  return [
    { id: 'AL-1', metric: 'voice.pulses', thresholdPct: 80, channels: ['inapp', 'email'], enabled: true },
    { id: 'AL-2', metric: 'msg.whatsapp', thresholdPct: 90, channels: ['inapp', 'email', 'sms'], enabled: true },
    { id: 'AL-3', metric: 'ai.interactions', thresholdPct: 85, channels: ['inapp'], enabled: true },
    { id: 'AL-4', metric: 'seats', thresholdPct: 90, channels: ['email'], enabled: false },
  ];
}

export function seedRateLimits(): RateLimitPolicy[] {
  return [
    { id: 'RL-api', scope: 'api', label: 'Public API', perMinute: 1200, current: 540, burst: 2400 },
    { id: 'RL-voice', scope: 'voice', label: 'Outbound voice dialler', perMinute: 90, current: 38, burst: 150 },
    { id: 'RL-wa', scope: 'whatsapp', label: 'WhatsApp send', perMinute: 600, current: 410, burst: 1000 },
    { id: 'RL-email', scope: 'email', label: 'Email send', perMinute: 800, current: 120, burst: 1500 },
  ];
}

export const CHANNEL_LABEL: Record<AlertChannel, string> = { inapp: 'In-app', email: 'Email', sms: 'SMS' };
