import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { PageHeaderComponent, SectionCardComponent } from '../../shared/ui/layout.component';
import { DrawerComponent } from '../../shared/ui/drawer.component';
import { BillingStore } from '../../data-access/billing.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { PLANS, PlanTier, METRIC_BY_KEY, monthlyAllowance, MetricKey } from '../../data-access/billing.data';
import { fmtDate, relFuture } from '../../shared/util/format';

const inr = (n: number) => '₹' + Math.round(n).toLocaleString('en-IN');
const compact = (n: number) => n >= 1e7 ? (n / 1e7).toFixed(2) + ' Cr' : n >= 1e5 ? (n / 1e5).toFixed(1) + ' L' : n.toLocaleString('en-IN');

@Component({
  selector: 'va-licensing',
  standalone: true,
  imports: [RouterLink, IconComponent, PageHeaderComponent, SectionCardComponent, DrawerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page page-grid">
      <va-page-header title="Plan & Licensing" [subtitle]="'Your Admission Counsellor subscription, entitlements and contract for ' + inst()">
        <a routerLink="/app/billing" class="btn btn-ghost"><va-icon name="file-text" [size]="16"></va-icon> Billing & usage</a>
        <button class="btn btn-primary" (click)="changeOpen.set(true)"><va-icon name="arrow-up-right" [size]="16"></va-icon> Request plan change</button>
      </va-page-header>

      <!-- Current plan hero -->
      <section class="plan-hero">
        <div class="ph-main">
          <div class="ph-badge"><va-icon name="award" [size]="14"></va-icon> {{ plan().name }} plan</div>
          <h2 class="ph-name">{{ inst() }}</h2>
          <p class="ph-tag">{{ plan().tagline }}</p>
          <div class="ph-price">
            <span class="now t-num">{{ inr(store.effectiveAnnualINR()) }}</span><span class="per">/ year</span>
            @if (store.discount.pct > 0) {
              <span class="was t-num">{{ inr(plan().annualPriceINR) }}</span>
              <span class="off">−{{ store.discount.pct }}%</span>
            }
          </div>
          <p class="ph-note">+ {{ store.tax.gstPercent }}% GST · billed annually · {{ store.discount.reason }}</p>
        </div>
        <div class="ph-facts">
          <div class="fact"><span class="fl">Status</span><span class="fv"><span class="dot live"></span> Active</span></div>
          <div class="fact"><span class="fl">Term</span><span class="fv">Annual</span></div>
          <div class="fact"><span class="fl">Started</span><span class="fv">{{ date(store.contractStart) }}</span></div>
          <div class="fact"><span class="fl">Renews</span><span class="fv">{{ date(store.renewalDate) }} <span class="t-muted">({{ renews() }})</span></span></div>
          <div class="fact"><span class="fl">Auto-renew</span><span class="fv ok"><va-icon name="check" [size]="13"></va-icon> On</span></div>
          <div class="fact"><span class="fl">Seats</span><span class="fv">{{ seatsUsed() }} / {{ seatsTotal() }}</span></div>
        </div>
      </section>

      <div class="cols">
        <!-- Entitlements -->
        <va-section-card title="What's included" hint="Capabilities enabled on your plan">
          <ul class="feat-list">
            @for (f of plan().featureMatrix; track f.key) {
              <li [class.off]="!f.included">
                <va-icon [name]="f.included ? 'check-circle' : 'minus-circle'" [size]="16"></va-icon>
                <span>{{ f.label }}</span>
                @if (!f.included) { <span class="up">Upgrade</span> }
              </li>
            }
          </ul>
        </va-section-card>

        <!-- Allowances -->
        <va-section-card title="Monthly allowances" hint="Included usage before overage applies">
          <div class="allow">
            @for (a of allowances(); track a.key) {
              <div class="al-row">
                <span class="al-ic"><va-icon [name]="a.icon" [size]="15"></va-icon></span>
                <span class="al-label">{{ a.label }}</span>
                <span class="al-val t-num">{{ a.display }}</span>
              </div>
            }
          </div>
          <a routerLink="/app/billing" class="btn btn-ghost btn-block see-usage"><va-icon name="gauge" [size]="15"></va-icon> See live usage & metering</a>
        </va-section-card>
      </div>

      <!-- Tier comparison -->
      <va-section-card title="Plans" hint="Compare tiers — your institution is on {{ plan().name }}">
        <div class="tiers">
          @for (p of plans; track p.tier) {
            <article class="tier" [class.current]="p.tier === store.tier()" [class.popular]="p.popular">
              @if (p.tier === store.tier()) { <span class="ribbon">Your plan</span> }
              @else if (p.popular) { <span class="ribbon pop">Popular</span> }
              <h3 class="t-h4">{{ p.name }}</h3>
              <div class="tier-price t-num">{{ inr(p.annualPriceINR) }}<span class="per">/yr</span></div>
              <p class="t-sm t-muted tier-tag">{{ p.tagline }}</p>
              <ul class="tier-quotas">
                <li><va-icon name="users" [size]="13"></va-icon> {{ seatsOf(p.tier) }} seats</li>
                <li><va-icon name="message-circle" [size]="13"></va-icon> {{ quota(p.tier, 'msg.whatsapp') }} WhatsApp / yr</li>
                <li><va-icon name="sparkles" [size]="13"></va-icon> {{ quota(p.tier, 'ai.interactions') }} AI interactions / yr</li>
                <li><va-icon name="video" [size]="13"></va-icon> {{ quota(p.tier, 'vcon.sessions') }} V-Cons / yr</li>
              </ul>
              @if (p.tier === store.tier()) {
                <button class="btn btn-ghost btn-block" disabled>Current plan</button>
              } @else {
                <button class="btn btn-block" [class.btn-primary]="rank(p.tier) > rank(store.tier())" [class.btn-ghost]="rank(p.tier) < rank(store.tier())" (click)="openChange(p.tier)">
                  {{ rank(p.tier) > rank(store.tier()) ? 'Upgrade' : 'Downgrade' }}
                </button>
              }
            </article>
          }
        </div>
      </va-section-card>

      <!-- Contract & billing entity -->
      <va-section-card title="Contract & billing details" hint="Tax registration and the legal entity you are billed by">
        <div class="contract">
          <div class="ct"><span class="cl">Billed by</span><span class="cv">{{ store.tax.legalName }}</span></div>
          <div class="ct"><span class="cl">GSTIN</span><span class="cv t-mono">{{ store.tax.gstin }}</span></div>
          <div class="ct"><span class="cl">Place of supply</span><span class="cv">{{ store.tax.placeOfSupply }}</span></div>
          <div class="ct"><span class="cl">Tax</span><span class="cv">GST {{ store.tax.gstPercent }}% ({{ store.tax.intraState ? 'CGST + SGST' : 'IGST' }})</span></div>
          <div class="ct"><span class="cl">Billing contact</span><span class="cv">{{ billingEmail() }}</span></div>
          <div class="ct"><span class="cl">Negotiated discount</span><span class="cv">{{ store.discount.pct }}% — {{ store.discount.reason }}</span></div>
        </div>
      </va-section-card>
    </div>

    <!-- Plan change drawer -->
    <va-drawer [open]="changeOpen()" title="Request a plan change" subtitle="Sent to your Admission Counsellor account manager" [width]="460" (close)="changeOpen.set(false)">
      <div class="field">
        <label class="label">Target plan</label>
        <div class="opts">
          @for (p of plans; track p.tier) {
            <button class="opt" [class.on]="targetTier() === p.tier" (click)="targetTier.set(p.tier)" [disabled]="p.tier === store.tier()">
              <span class="o-name">{{ p.name }} @if (p.tier === store.tier()) { <span class="t-muted">(current)</span> }</span>
              <span class="o-price t-num">{{ inr(p.annualPriceINR) }}/yr</span>
            </button>
          }
        </div>
      </div>
      <div class="field" style="margin-top:14px">
        <label class="label">Note to your account manager (optional)</label>
        <textarea class="textarea" rows="3" [value]="note()" (input)="note.set($any($event.target).value)" placeholder="e.g. We're adding a second campus for Fall 2027 intake…"></textarea>
      </div>
      <div class="banner info" style="margin-top:14px">
        <va-icon name="info" [size]="16"></va-icon>
        <span>Plan changes apply from your next billing cycle. Included allowances adjust immediately on approval; your account manager confirms pricing.</span>
      </div>
      <div footer>
        <button class="btn btn-ghost" (click)="changeOpen.set(false)">Cancel</button>
        <button class="btn btn-primary" (click)="submitChange()"><va-icon name="send" [size]="15"></va-icon> Send request</button>
      </div>
    </va-drawer>`,
  styles: [`
    .plan-hero { display: grid; grid-template-columns: 1.4fr 1fr; gap: 28px; background: var(--gradient-ai); color: #06121A;
      border-radius: var(--r-lg); padding: 28px 30px; box-shadow: var(--e2); }
    .ph-badge { display: inline-flex; align-items: center; gap: 6px; background: rgba(6,18,26,.14); padding: 5px 11px; border-radius: var(--r-pill); font-size: var(--text-cap); font-weight: 700; }
    .ph-name { font-family: var(--font-display); font-size: 2rem; font-weight: 800; margin: 12px 0 4px; }
    .ph-tag { font-size: var(--text-sm); opacity: .8; max-width: 46ch; }
    .ph-price { display: flex; align-items: baseline; gap: 10px; margin-top: 18px; flex-wrap: wrap; }
    .ph-price .now { font-family: var(--font-display); font-size: 2.1rem; font-weight: 800; }
    .ph-price .per { font-size: var(--text-sm); opacity: .75; }
    .ph-price .was { text-decoration: line-through; opacity: .55; font-size: 1.1rem; }
    .ph-price .off { background: #06121A; color: #fff; font-size: var(--text-cap); font-weight: 700; padding: 3px 8px; border-radius: var(--r-pill); }
    .ph-note { font-size: var(--text-cap); opacity: .8; margin-top: 8px; }
    .ph-facts { display: flex; flex-direction: column; gap: 0; background: rgba(255,255,255,.5); border-radius: var(--r-md); padding: 4px 14px; align-self: center; }
    .fact { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 9px 0; border-bottom: 1px solid rgba(6,18,26,.1); font-size: var(--text-sm); }
    .fact:last-child { border-bottom: none; }
    .fact .fl { opacity: .7; font-weight: 500; }
    .fact .fv { font-weight: 700; display: inline-flex; align-items: center; gap: 6px; }
    .fact .fv.ok { color: #047857; }
    .fact .dot { width: 8px; height: 8px; }

    .cols { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-6); }
    .feat-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 11px; }
    .feat-list li { display: flex; align-items: center; gap: 10px; font-size: var(--text-sm); font-weight: 500; }
    .feat-list li va-icon { color: var(--color-success); flex: none; }
    .feat-list li.off { color: var(--color-text-muted); }
    .feat-list li.off va-icon { color: var(--color-border-strong); }
    .feat-list .up { margin-left: auto; font-size: 10px; font-weight: 700; color: var(--color-primary); background: rgba(var(--color-primary-rgb),.1); padding: 2px 7px; border-radius: var(--r-pill); }

    .allow { display: grid; gap: 2px; }
    .al-row { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px dashed var(--color-border); }
    .al-row:last-child { border-bottom: none; }
    .al-ic { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center; background: var(--color-surface-alt); color: var(--color-text-muted); flex: none; }
    .al-label { font-size: var(--text-sm); font-weight: 500; }
    .al-val { margin-left: auto; font-weight: 700; }
    .see-usage { margin-top: 14px; }

    .tiers { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    .tier { position: relative; border: 1px solid var(--color-border); border-radius: var(--r-lg); padding: 22px; display: flex; flex-direction: column; gap: 6px; }
    .tier.current { border-color: var(--color-primary); box-shadow: 0 0 0 1px var(--color-primary); }
    .tier.popular:not(.current) { border-color: var(--color-accent-2); }
    .ribbon { position: absolute; top: 14px; right: 14px; font-size: 10px; font-weight: 700; padding: 3px 9px; border-radius: var(--r-pill); background: var(--color-primary); color: #fff; }
    .ribbon.pop { background: var(--color-accent-2); color: #06121A; }
    .tier-price { font-family: var(--font-display); font-size: 1.5rem; font-weight: 800; }
    .tier-price .per { font-size: var(--text-sm); font-weight: 500; color: var(--color-text-muted); }
    .tier-tag { min-height: 38px; margin: 2px 0 8px; }
    .tier-quotas { list-style: none; padding: 0; margin: 0 0 14px; display: grid; gap: 8px; }
    .tier-quotas li { display: flex; align-items: center; gap: 8px; font-size: var(--text-sm); }
    .tier-quotas li va-icon { color: var(--color-text-muted); }
    .tier .btn { margin-top: auto; }

    .contract { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 28px; }
    .ct { display: flex; flex-direction: column; gap: 3px; }
    .cl { font-size: var(--text-cap); color: var(--color-text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
    .cv { font-size: var(--text-sm); font-weight: 600; }

    .opts { display: grid; gap: 8px; }
    .opt { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 14px; border: 1px solid var(--color-border); border-radius: var(--r-md); background: var(--color-surface); cursor: pointer; transition: all .15s; }
    .opt:hover:not(:disabled) { border-color: var(--color-primary); }
    .opt.on { border-color: var(--color-primary); background: rgba(var(--color-primary-rgb),.07); }
    .opt:disabled { opacity: .5; cursor: not-allowed; }
    .o-name { font-weight: 600; font-size: var(--text-sm); }
    .o-price { font-weight: 700; }

    @media (max-width: 1000px) { .plan-hero, .cols { grid-template-columns: 1fr; } .tiers { grid-template-columns: 1fr; } .contract { grid-template-columns: 1fr; } }
  `],
})
export class LicensingComponent {
  store = inject(BillingStore);
  private auth = inject(AuthService);
  private toast = inject(ToastService);
  plans = PLANS;

  inr = inr;
  date = fmtDate;
  plan = this.store.plan;
  inst = computed(() => this.auth.institution().name);
  renews = computed(() => relFuture(this.store.renewalDate + 'T00:00:00'));
  billingEmail = computed(() => 'billing@' + this.auth.tenantDomain());

  seatsTotal = computed(() => this.plan().quotas.find(x => x.metric === 'seats')?.included ?? 0);
  seatsUsed = computed(() => this.store.meterRows().find(r => r.key === 'seats')?.used ?? 0);

  allowances = computed(() => (['ai.interactions', 'msg.whatsapp', 'voice.pulses', 'voice.calls', 'vcon.minutes', 'email.sent'] as MetricKey[]).map(k => {
    const def = METRIC_BY_KEY[k];
    return { key: k, icon: def.icon, label: def.label, display: compact(monthlyAllowance(this.plan(), k)) + ' / mo' };
  }));

  rank(t: PlanTier) { return ({ startup: 1, growth: 2, enterprise: 3 } as const)[t]; }
  seatsOf(t: PlanTier) { return compact(PLANS.find(p => p.tier === t)!.quotas.find(x => x.metric === 'seats')!.included); }
  quota(t: PlanTier, m: MetricKey) { return compact(PLANS.find(p => p.tier === t)!.quotas.find(x => x.metric === m)!.included); }

  // plan change drawer
  changeOpen = signal(false);
  targetTier = signal<PlanTier>('enterprise');
  note = signal('');
  openChange(t: PlanTier) { this.targetTier.set(t); this.changeOpen.set(true); }
  submitChange() {
    const name = PLANS.find(p => p.tier === this.targetTier())?.name;
    this.changeOpen.set(false);
    this.note.set('');
    this.toast.success(`Plan change to ${name} requested — your account manager will confirm shortly.`);
  }
}
