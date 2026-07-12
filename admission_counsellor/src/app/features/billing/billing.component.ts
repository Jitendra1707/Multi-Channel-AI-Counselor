import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { PageHeaderComponent, SectionCardComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { DrawerComponent } from '../../shared/ui/drawer.component';
import { BillingStore } from '../../data-access/billing.store';
import { AuthService } from '../../core/auth.service';
import { ToastService } from '../../core/toast.service';
import { Invoice, MeterRow, MetricKey, METRICS, METRIC_BY_KEY, CHANNEL_LABEL, AlertChannel } from '../../data-access/billing.data';
import { fmtDate, relFuture } from '../../shared/util/format';

const inr = (n: number) => '₹' + Math.round(n).toLocaleString('en-IN');
const compact = (n: number) => n >= 1e7 ? (n / 1e7).toFixed(2) + ' Cr' : n >= 1e5 ? (n / 1e5).toFixed(2) + ' L' : n.toLocaleString('en-IN');
const TODAY = '2026-06-16';
type Tab = 'usage' | 'invoices' | 'credits' | 'alerts';

@Component({
  selector: 'va-billing',
  standalone: true,
  imports: [RouterLink, IconComponent, PageHeaderComponent, SectionCardComponent, EmptyStateComponent, DrawerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page page-grid">
      <va-page-header title="Billing & Usage" [subtitle]="'Metering, invoices, credits and limits for ' + inst()">
        <a routerLink="/app/licensing" class="btn btn-ghost"><va-icon name="award" [size]="16"></va-icon> Plan & licensing</a>
        @if (store.outstanding().length) {
          <button class="btn btn-primary" (click)="openPay(store.nextDue()!)"><va-icon name="credit-card" [size]="16"></va-icon> Pay {{ money(store.nextDue()!.totalINR) }}</button>
        }
      </va-page-header>

      <!-- KPI strip -->
      <div class="kpis">
        <div class="kpi">
          <span class="k-ic" style="background:rgba(var(--color-primary-rgb),.1);color:var(--color-primary)"><va-icon name="award" [size]="18"></va-icon></span>
          <div><div class="k-label">{{ store.plan().name }} plan</div><div class="k-val t-num">{{ money(store.effectiveAnnualINR()) }}<span class="k-sub">/yr</span></div></div>
        </div>
        <div class="kpi">
          <span class="k-ic" style="background:var(--color-warning-soft);color:var(--color-warning)"><va-icon name="trending-up" [size]="18"></va-icon></span>
          <div><div class="k-label">This cycle overage · {{ store.cycleLabel }}</div><div class="k-val t-num">{{ money(store.cycleOverageINR()) }}<span class="k-sub">+ GST</span></div></div>
        </div>
        <div class="kpi">
          <span class="k-ic" style="background:var(--color-success-soft);color:var(--color-success)"><va-icon name="wallet" [size]="18"></va-icon></span>
          <div><div class="k-label">Credit balance</div><div class="k-val t-num">{{ money(store.creditBalance()) }}<span class="k-sub">~{{ store.creditCoverageDays() }}d cover</span></div></div>
        </div>
        <div class="kpi" [class.alarm]="store.outstanding().length">
          <span class="k-ic" [style.background]="store.outstanding().length ? 'var(--color-danger-soft)' : 'var(--color-surface-alt)'" [style.color]="store.outstanding().length ? 'var(--color-danger)' : 'var(--color-text-muted)'"><va-icon name="receipt" [size]="18"></va-icon></span>
          <div><div class="k-label">Outstanding</div><div class="k-val t-num">{{ money(store.outstandingINR()) }}@if (store.nextDue()){<span class="k-sub">due {{ due(store.nextDue()!.due) }}</span>}</div></div>
        </div>
      </div>

      <!-- Tabs -->
      <div class="tabs">
        <button class="tab" [class.active]="tab() === 'usage'" (click)="tab.set('usage')">Usage & metering</button>
        <button class="tab" [class.active]="tab() === 'invoices'" (click)="tab.set('invoices')">Invoices & payments<span class="count">{{ store.invoices().length }}</span></button>
        <button class="tab" [class.active]="tab() === 'credits'" (click)="tab.set('credits')">Credits</button>
        <button class="tab" [class.active]="tab() === 'alerts'" (click)="tab.set('alerts')">Alerts & rate limits</button>
      </div>

      <!-- ===== USAGE & METERING ===== -->
      @if (tab() === 'usage') {
        @if (store.overMetrics().length) {
          <div class="banner warning">
            <va-icon name="alert-triangle" [size]="18"></va-icon>
            <div><strong>{{ store.overMetrics().length }} metric(s) are over the monthly allowance this cycle.</strong>
              <span class="t-sm"> Overage of {{ money(store.cycleOverageINR()) }} is being auto-covered by your credit balance, so service continues uninterrupted.</span></div>
          </div>
        }
        <div class="usage-grid">
          <va-section-card title="Metered usage — {{ store.cycleLabel }}" hint="Consumption vs included monthly allowance" [flush]="true">
            <div class="table-wrap"><table class="tbl">
              <thead><tr><th>Metric</th><th class="num">Used</th><th class="num">Allowance</th><th style="width:180px">Load</th><th class="num">Overage</th><th class="num">Est. ₹</th></tr></thead>
              <tbody>
                @for (r of store.meterRows(); track r.key) {
                  <tr>
                    <td><div class="m-name"><va-icon [name]="r.def.icon" [size]="15"></va-icon> {{ r.def.label }}</div></td>
                    <td class="num t-num">{{ num(r.used) }} <span class="t-muted t-cap">{{ r.def.unit }}</span></td>
                    <td class="num t-num t-muted">{{ num(r.allowance) }}</td>
                    <td><div class="meter"><span [class]="'fill ' + bandOf(r)" [style.width.%]="capW(r.loadPct)"></span></div><span class="m-pct">{{ r.loadPct }}%</span></td>
                    <td class="num t-num">@if (r.over > 0) { <span class="over">+{{ num(r.over) }}</span> } @else { <span class="t-muted">—</span> }</td>
                    <td class="num t-num">@if (r.overINR > 0) { {{ money(r.overINR) }} } @else { <span class="t-muted">—</span> }</td>
                  </tr>
                }
              </tbody>
            </table></div>
          </va-section-card>

          <va-section-card title="This cycle estimate">
            <div class="est">
              <div class="er"><span>Overage subtotal</span><span class="t-num">{{ money(store.cycleOverageINR()) }}</span></div>
              <div class="er"><span>Discount ({{ store.discount.pct }}%)</span><span class="t-num neg">−{{ money(disc()) }}</span></div>
              <div class="er"><span>Taxable</span><span class="t-num">{{ money(store.cycleEstimate().taxableINR) }}</span></div>
              <div class="er sub"><span>CGST {{ store.tax.cgstPercent }}%</span><span class="t-num">{{ money(store.cycleEstimate().cgstINR) }}</span></div>
              <div class="er sub"><span>SGST {{ store.tax.sgstPercent }}%</span><span class="t-num">{{ money(store.cycleEstimate().sgstINR) }}</span></div>
              <div class="er total"><span>Estimated bill</span><span class="t-num">{{ money(store.cycleEstimate().totalINR) }}</span></div>
            </div>
            <div class="cover">
              <va-icon name="shield-check" [size]="15"></va-icon>
              <span>Covered by {{ money(store.creditBalance()) }} in credits — about {{ store.creditCoverageDays() }} days of headroom at the current rate.</span>
            </div>
            <button class="btn btn-ghost btn-block" (click)="tab.set('credits')"><va-icon name="plus" [size]="15"></va-icon> Load more credits</button>
          </va-section-card>
        </div>
      }

      <!-- ===== INVOICES & PAYMENTS ===== -->
      @if (tab() === 'invoices') {
        @if (store.outstanding().length) {
          <div class="banner danger">
            <va-icon name="alert-circle" [size]="18"></va-icon>
            <div><strong>{{ money(store.outstandingINR()) }} outstanding across {{ store.outstanding().length }} invoice(s).</strong>
              <span class="t-sm"> Pay before the due date to avoid service rate-limiting.</span></div>
            <button class="btn btn-sm btn-danger" (click)="openPay(store.nextDue()!)">Pay now</button>
          </div>
        }
        <va-section-card title="Invoices" hint="Annual licence and monthly usage invoices, with GST" [flush]="true">
          <div class="table-wrap"><table class="tbl">
            <thead><tr><th>Invoice</th><th>Period</th><th>Issued</th><th>Due</th><th class="num">Amount</th><th>Status</th><th></th></tr></thead>
            <tbody>
              @for (i of store.invoices(); track i.id) {
                <tr class="clickable" (click)="openInvoice(i)">
                  <td class="t-mono t-sm">{{ i.id }}</td>
                  <td>{{ i.period }}</td>
                  <td class="t-muted t-sm">{{ date(i.issued) }}</td>
                  <td class="t-muted t-sm">{{ date(i.due) }}</td>
                  <td class="num t-num" style="font-weight:700">{{ money(i.totalINR) }}</td>
                  <td><span class="st" [attr.data-s]="i.status">{{ i.status }}</span></td>
                  <td class="num">
                    @if (i.status === 'due' || i.status === 'overdue') {
                      <button class="btn btn-sm btn-primary" (click)="openPay(i); $event.stopPropagation()">Pay</button>
                    } @else {
                      <button class="btn btn-sm btn-ghost" (click)="openInvoice(i); $event.stopPropagation()">View</button>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table></div>
        </va-section-card>

        <va-section-card title="Payment history" hint="Receipts for invoices and credit top-ups" [flush]="true">
          <div class="table-wrap"><table class="tbl">
            <thead><tr><th>Date</th><th>For</th><th>Method</th><th>Reference</th><th class="num">Amount</th></tr></thead>
            <tbody>
              @for (p of store.payments(); track p.id) {
                <tr><td class="t-muted t-sm">{{ date(p.date) }}</td><td>{{ p.label }}</td><td>{{ p.method }}</td><td class="t-mono t-sm t-muted">{{ p.reference }}</td><td class="num t-num" style="font-weight:700">{{ money(p.amountINR) }}</td></tr>
              }
            </tbody>
          </table></div>
        </va-section-card>
      }

      <!-- ===== CREDITS ===== -->
      @if (tab() === 'credits') {
        <div class="credits-grid">
          <va-section-card title="Credit wallet" hint="Prepaid balance that auto-covers overage for business continuity">
            <div class="wallet">
              <div class="w-bal"><span class="wb-label">Available balance</span><span class="wb-val t-num">{{ money(store.creditBalance()) }}</span></div>
              <div class="w-cover"><va-icon name="shield-check" [size]="14"></va-icon> ~{{ store.creditCoverageDays() }} days of overage headroom at the current run-rate</div>
            </div>
            <div class="quick">
              <span class="q-label">Quick top-up</span>
              <div class="q-btns">
                @for (a of quickAmounts; track a) { <button class="q-amt" (click)="openTopup(a)">{{ money(a) }}</button> }
                <button class="q-amt custom" (click)="openTopup(0)">Custom…</button>
              </div>
            </div>
            <div class="auto">
              <div class="auto-head">
                <div><div class="t-sm" style="font-weight:600">Auto-recharge</div><div class="t-cap t-muted">Keep credits topped up so service never pauses</div></div>
                <button class="toggle" [class.on]="store.autoRecharge().enabled" (click)="store.setAutoRecharge({ enabled: !store.autoRecharge().enabled })" [attr.aria-pressed]="store.autoRecharge().enabled"><span class="knob"></span></button>
              </div>
              @if (store.autoRecharge().enabled) {
                <div class="auto-cfg">
                  <span>When balance falls below <strong class="t-num">{{ money(store.autoRecharge().thresholdINR) }}</strong>, top up <strong class="t-num">{{ money(store.autoRecharge().topupINR) }}</strong>.</span>
                </div>
              }
            </div>
          </va-section-card>

          <va-section-card title="Credit ledger" hint="Top-ups, bonuses and overage drawdowns" [flush]="true">
            <div class="table-wrap"><table class="tbl">
              <thead><tr><th>Date</th><th>Activity</th><th class="num">Amount</th></tr></thead>
              <tbody>
                @for (c of store.credits(); track c.id) {
                  <tr>
                    <td class="t-muted t-sm">{{ date(c.date) }}</td>
                    <td><span class="ck" [attr.data-k]="c.kind"><va-icon [name]="c.kind === 'consume' ? 'arrow-down-right' : 'arrow-up-right'" [size]="13"></va-icon></span> {{ c.label }}</td>
                    <td class="num t-num" [class.neg]="c.amountINR < 0" [class.pos]="c.amountINR > 0" style="font-weight:700">{{ c.amountINR < 0 ? '−' : '+' }}{{ money(absv(c.amountINR)) }}</td>
                  </tr>
                }
              </tbody>
            </table></div>
          </va-section-card>
        </div>
      }

      <!-- ===== ALERTS & RATE LIMITS ===== -->
      @if (tab() === 'alerts') {
        <va-section-card title="Usage threshold alerts" hint="Get notified before a metric runs into overage">
          <button actions class="btn btn-sm btn-primary" (click)="openAlert()"><va-icon name="plus" [size]="14"></va-icon> Add alert</button>
          @if (store.alerts().length) {
            <div class="alerts">
              @for (a of store.alerts(); track a.id) {
                <div class="alert" [class.off]="!a.enabled">
                  <span class="a-ic"><va-icon [name]="metricIcon(a.metric)" [size]="16"></va-icon></span>
                  <div class="a-body">
                    <div class="a-title">{{ metricLabel(a.metric) }} <span class="a-th">at {{ a.thresholdPct }}%</span></div>
                    <div class="a-ch">Notify: {{ channelText(a.channels) }}</div>
                  </div>
                  <button class="toggle sm" [class.on]="a.enabled" (click)="store.toggleAlert(a.id)" [attr.aria-pressed]="a.enabled"><span class="knob"></span></button>
                  <button class="btn btn-icon btn-ghost btn-sm" (click)="openAlert(a.id)" aria-label="Edit"><va-icon name="edit" [size]="15"></va-icon></button>
                  <button class="btn btn-icon btn-ghost btn-sm" (click)="store.removeAlert(a.id)" aria-label="Remove"><va-icon name="trash-2" [size]="15"></va-icon></button>
                </div>
              }
            </div>
          } @else {
            <va-empty icon="bell" title="No alerts yet" message="Add a threshold alert to be warned before a metric reaches its limit." cta="Add alert" ctaIcon="plus" (action)="openAlert()"></va-empty>
          }
        </va-section-card>

        <va-section-card title="Rate-limit thresholds" hint="Protect spend and stability — cap requests per minute by channel">
          <div class="rls">
            @for (r of store.rateLimits(); track r.id) {
              <div class="rl">
                <div class="rl-top">
                  <span class="rl-name"><va-icon [name]="scopeIcon(r.scope)" [size]="15"></va-icon> {{ r.label }}</span>
                  <span class="rl-load t-cap" [class.hot]="loadPct(r.current, r.perMinute) >= 80">{{ r.current }} / {{ r.perMinute }} per min · {{ loadPct(r.current, r.perMinute) }}%</span>
                </div>
                <div class="meter"><span class="fill" [class.amber]="loadPct(r.current,r.perMinute)>=70" [class.red]="loadPct(r.current,r.perMinute)>=90" [style.width.%]="capW(loadPct(r.current, r.perMinute))"></span></div>
                <div class="rl-edit">
                  <label class="t-cap t-muted">Limit (req/min)</label>
                  <input class="input rl-in" type="number" [value]="r.perMinute" (change)="saveLimit(r.id, $any($event.target).value)">
                  <span class="t-cap t-muted">Burst {{ r.burst }}</span>
                </div>
              </div>
            }
          </div>
        </va-section-card>
      }
    </div>

    <!-- Invoice detail drawer -->
    <va-drawer [open]="!!invoiceItem()" [title]="invoiceItem()?.id || ''" [subtitle]="invoiceItem()?.period || ''" [width]="500" (close)="invoiceItem.set(null)">
      @if (invoiceItem(); as i) {
        <div class="inv-head">
          <span class="st" [attr.data-s]="i.status">{{ i.status }}</span>
          <div class="t-cap t-muted">Issued {{ date(i.issued) }} · Due {{ date(i.due) }}@if (i.paidOn) { · Paid {{ date(i.paidOn) }} ({{ i.method }})}</div>
        </div>
        <table class="tbl inv-lines">
          <thead><tr><th>Description</th><th class="num">Amount</th></tr></thead>
          <tbody>
            @for (l of i.lines; track l.label) { <tr><td>{{ l.label }}</td><td class="num t-num">{{ money(l.amountINR) }}</td></tr> }
          </tbody>
        </table>
        <div class="est" style="margin-top:14px">
          <div class="er"><span>Subtotal</span><span class="t-num">{{ money(i.subtotalINR) }}</span></div>
          @if (i.discountINR > 0) { <div class="er"><span>Discount ({{ i.discountPercent }}%)</span><span class="t-num neg">−{{ money(i.discountINR) }}</span></div> }
          <div class="er"><span>Taxable</span><span class="t-num">{{ money(i.taxableINR) }}</span></div>
          @if (i.cgstINR > 0) { <div class="er sub"><span>CGST {{ store.tax.cgstPercent }}%</span><span class="t-num">{{ money(i.cgstINR) }}</span></div>
            <div class="er sub"><span>SGST {{ store.tax.sgstPercent }}%</span><span class="t-num">{{ money(i.sgstINR) }}</span></div> }
          @if (i.igstINR > 0) { <div class="er sub"><span>IGST {{ store.tax.igstPercent }}%</span><span class="t-num">{{ money(i.igstINR) }}</span></div> }
          <div class="er total"><span>Total</span><span class="t-num">{{ money(i.totalINR) }}</span></div>
        </div>
      }
      <div footer>
        @if (invoiceItem(); as i) {
          <button class="btn btn-ghost" (click)="invoiceItem.set(null)">Close</button>
          @if (i.status === 'due' || i.status === 'overdue') {
            <button class="btn btn-primary" (click)="openPay(i)"><va-icon name="credit-card" [size]="15"></va-icon> Pay {{ money(i.totalINR) }}</button>
          } @else { <button class="btn btn-ghost" (click)="downloadNote()"><va-icon name="download" [size]="15"></va-icon> Download PDF</button> }
        }
      </div>
    </va-drawer>

    <!-- Pay drawer -->
    <va-drawer [open]="!!payItem()" title="Pay invoice" [subtitle]="payItem()?.id || ''" [width]="440" (close)="payItem.set(null)">
      @if (payItem(); as i) {
        <div class="pay-amt"><span class="t-cap t-muted">Amount due</span><span class="pa-val t-num">{{ money(i.totalINR) }}</span><span class="t-cap t-muted">{{ i.period }} · incl. GST</span></div>
        <div class="field"><label class="label">Payment method</label>
          <div class="opts">
            @for (m of methods; track m) { <button class="opt" [class.on]="payMethod() === m" (click)="payMethod.set(m)"><va-icon [name]="methodIcon(m)" [size]="16"></va-icon> {{ m }}</button> }
          </div>
        </div>
        <div class="banner ai" style="margin-top:14px"><va-icon name="lock" [size]="15"></va-icon><span>Secured by Razorpay. Cards, UPI and net-banking are processed by the gateway. Admission Counsellor never stores card data.</span></div>
      }
      <div footer>
        @if (payItem(); as i) {
          <button class="btn btn-ghost" (click)="payItem.set(null)">Cancel</button>
          <button class="btn btn-primary" (click)="confirmPay()"><va-icon name="shield-check" [size]="15"></va-icon> Pay {{ money(i.totalINR) }}</button>
        }
      </div>
    </va-drawer>

    <!-- Top-up drawer -->
    <va-drawer [open]="topupOpen()" title="Load credits" subtitle="Prepay to keep service running through overage" [width]="440" (close)="topupOpen.set(false)">
      <div class="field"><label class="label">Amount (excl. GST)</label>
        <div class="q-btns" style="margin-bottom:10px">
          @for (a of quickAmounts; track a) { <button class="q-amt" [class.on]="topupAmt() === a" (click)="topupAmt.set(a)">{{ money(a) }}</button> }
        </div>
        <input class="input" type="number" [value]="topupAmt()" (input)="topupAmt.set(+$any($event.target).value || 0)" placeholder="Enter amount">
      </div>
      <div class="field" style="margin-top:14px"><label class="label">Payment method</label>
        <div class="opts">
          @for (m of methods; track m) { <button class="opt" [class.on]="payMethod() === m" (click)="payMethod.set(m)"><va-icon [name]="methodIcon(m)" [size]="16"></va-icon> {{ m }}</button> }
        </div>
      </div>
      <div class="est" style="margin-top:14px">
        <div class="er"><span>Credits</span><span class="t-num">{{ money(topupAmt()) }}</span></div>
        <div class="er sub"><span>GST {{ store.tax.gstPercent }}%</span><span class="t-num">{{ money(topupTax()) }}</span></div>
        <div class="er total"><span>You pay</span><span class="t-num">{{ money(topupAmt() + topupTax()) }}</span></div>
      </div>
      <div footer>
        <button class="btn btn-ghost" (click)="topupOpen.set(false)">Cancel</button>
        <button class="btn btn-primary" [disabled]="topupAmt() <= 0" (click)="confirmTopup()"><va-icon name="wallet" [size]="15"></va-icon> Pay {{ money(topupAmt() + topupTax()) }}</button>
      </div>
    </va-drawer>

    <!-- Alert drawer -->
    <va-drawer [open]="alertOpen()" [title]="editingAlert() ? 'Edit alert' : 'Add usage alert'" subtitle="Warn before a metric hits its limit" [width]="440" (close)="alertOpen.set(false)">
      <div class="field"><label class="label">Metric</label>
        <select class="select" (change)="alMetric.set($any($event.target).value)">
          @for (m of metrics; track m.key) { <option [value]="m.key" [selected]="m.key === alMetric()">{{ m.label }}</option> }
        </select>
      </div>
      <div class="field" style="margin-top:14px"><label class="label">Notify at {{ alThreshold() }}% of allowance</label>
        <input type="range" min="50" max="100" step="5" [value]="alThreshold()" (input)="alThreshold.set(+$any($event.target).value)" class="range">
      </div>
      <div class="field" style="margin-top:14px"><label class="label">Channels</label>
        <div class="opts row-opts">
          @for (c of channels; track c) { <button class="opt" [class.on]="alChannels().includes(c)" (click)="toggleCh(c)"><va-icon [name]="chIcon(c)" [size]="15"></va-icon> {{ chLabel(c) }}</button> }
        </div>
      </div>
      <div footer>
        <button class="btn btn-ghost" (click)="alertOpen.set(false)">Cancel</button>
        <button class="btn btn-primary" (click)="saveAlert()"><va-icon name="check" [size]="15"></va-icon> {{ editingAlert() ? 'Save' : 'Add alert' }}</button>
      </div>
    </va-drawer>`,
  styles: [`
    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--s-4); }
    .kpi { display: flex; align-items: center; gap: 12px; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-lg); padding: 16px 18px; box-shadow: var(--e1); }
    .kpi.alarm { border-color: color-mix(in srgb, var(--color-danger) 40%, transparent); }
    .k-ic { width: 40px; height: 40px; border-radius: 11px; display: grid; place-items: center; flex: none; }
    .k-label { font-size: var(--text-cap); color: var(--color-text-muted); font-weight: 600; }
    .k-val { font-family: var(--font-display); font-size: 1.4rem; font-weight: 800; line-height: 1.1; }
    .k-sub { font-size: var(--text-cap); font-weight: 600; color: var(--color-text-muted); margin-left: 6px; }

    .usage-grid, .credits-grid { display: grid; grid-template-columns: 1.7fr 1fr; gap: var(--s-6); align-items: start; }

    .table-wrap { overflow-x: auto; }
    .tbl { width: 100%; border-collapse: collapse; font-size: var(--text-sm); }
    .tbl th { text-align: left; font-size: var(--text-cap); text-transform: uppercase; letter-spacing: .04em; color: var(--color-text-muted); font-weight: 700; padding: 10px 14px; border-bottom: 1px solid var(--color-border); position: sticky; top: 0; background: var(--color-surface); }
    .tbl th.num, .tbl td.num { text-align: right; }
    .tbl td { padding: 11px 14px; border-bottom: 1px solid var(--color-border); }
    .tbl tbody tr:last-child td { border-bottom: none; }
    .tbl tr.clickable { cursor: pointer; }
    .tbl tr.clickable:hover { background: var(--color-surface-alt); }
    .m-name { display: flex; align-items: center; gap: 9px; font-weight: 600; }
    .m-name va-icon { color: var(--color-text-muted); }
    .over { color: var(--color-warning); font-weight: 700; }

    .meter { height: 7px; border-radius: 999px; background: var(--color-surface-alt); overflow: hidden; display: inline-block; width: 100%; vertical-align: middle; }
    .meter .fill { display: block; height: 100%; border-radius: 999px; background: var(--color-success); }
    .meter .fill.amber { background: var(--color-warning); }
    .meter .fill.red { background: var(--color-danger); }
    .m-pct { font-size: var(--text-cap); color: var(--color-text-muted); margin-left: 8px; font-variant-numeric: tabular-nums; }

    .est { display: grid; gap: 8px; }
    .er { display: flex; align-items: center; justify-content: space-between; font-size: var(--text-sm); }
    .er.sub { font-size: var(--text-cap); color: var(--color-text-muted); }
    .er.total { border-top: 1px solid var(--color-border); padding-top: 10px; margin-top: 4px; font-weight: 800; font-size: var(--text-base); }
    .neg { color: var(--color-success); }
    .pos { color: var(--color-success); }
    .cover { display: flex; gap: 8px; align-items: flex-start; font-size: var(--text-cap); color: var(--color-text-muted); margin: 14px 0; padding: 10px 12px; background: var(--color-success-soft); border-radius: var(--r-md); }
    .cover va-icon { color: var(--color-success); flex: none; }

    .st { display: inline-flex; font-size: var(--text-cap); font-weight: 700; padding: 3px 9px; border-radius: var(--r-pill); text-transform: capitalize; }
    .st[data-s='paid'] { background: var(--color-success-soft); color: var(--color-success); }
    .st[data-s='due'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .st[data-s='overdue'] { background: var(--color-danger-soft); color: var(--color-danger); }
    .st[data-s='draft'] { background: var(--color-surface-alt); color: var(--color-text-muted); }

    .wallet { background: var(--gradient-ai); color: #06121A; border-radius: var(--r-md); padding: 20px; margin-bottom: 16px; }
    .wb-label { font-size: var(--text-cap); font-weight: 600; opacity: .8; display: block; }
    .wb-val { font-family: var(--font-display); font-size: 2rem; font-weight: 800; display: block; margin: 2px 0 8px; }
    .w-cover { font-size: var(--text-cap); font-weight: 600; display: flex; align-items: center; gap: 6px; }
    .quick { margin-bottom: 16px; }
    .q-label { font-size: var(--text-sm); font-weight: 600; display: block; margin-bottom: 8px; }
    .q-btns { display: flex; flex-wrap: wrap; gap: 8px; }
    .q-amt { padding: 8px 14px; border: 1px solid var(--color-border); border-radius: var(--r-md); background: var(--color-surface); font-weight: 700; font-size: var(--text-sm); cursor: pointer; transition: all .15s; font-variant-numeric: tabular-nums; }
    .q-amt:hover { border-color: var(--color-primary); color: var(--color-primary); }
    .q-amt.on { border-color: var(--color-primary); background: rgba(var(--color-primary-rgb),.08); color: var(--color-primary); }
    .q-amt.custom { color: var(--color-text-muted); }
    .auto { border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 14px; }
    .auto-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .auto-cfg { margin-top: 10px; font-size: var(--text-sm); color: var(--color-text-muted); }

    .toggle { width: 40px; height: 23px; border-radius: 999px; background: var(--color-border-strong); border: none; position: relative; cursor: pointer; transition: background .18s; flex: none; }
    .toggle.sm { width: 36px; height: 20px; }
    .toggle .knob { position: absolute; top: 2px; left: 2px; width: 19px; height: 19px; border-radius: 50%; background: #fff; transition: transform .18s; }
    .toggle.sm .knob { width: 16px; height: 16px; }
    .toggle.on { background: var(--color-primary); }
    .toggle.on .knob { transform: translateX(17px); }
    .toggle.sm.on .knob { transform: translateX(16px); }

    .ck { display: inline-flex; width: 20px; height: 20px; border-radius: 6px; align-items: center; justify-content: center; vertical-align: middle; margin-right: 4px; }
    .ck[data-k='topup'], .ck[data-k='bonus'] { background: var(--color-success-soft); color: var(--color-success); }
    .ck[data-k='consume'] { background: var(--color-warning-soft); color: var(--color-warning); }

    .alerts { display: grid; gap: 10px; }
    .alert { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border: 1px solid var(--color-border); border-radius: var(--r-md); }
    .alert.off { opacity: .55; }
    .a-ic { width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center; background: var(--color-surface-alt); color: var(--color-text-muted); flex: none; }
    .a-body { flex: 1; }
    .a-title { font-size: var(--text-sm); font-weight: 600; }
    .a-th { color: var(--color-primary); font-weight: 700; }
    .a-ch { font-size: var(--text-cap); color: var(--color-text-muted); }

    .rls { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .rl { border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 14px; }
    .rl-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
    .rl-name { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: var(--text-sm); }
    .rl-name va-icon { color: var(--color-text-muted); }
    .rl-load { color: var(--color-text-muted); }
    .rl-load.hot { color: var(--color-warning); font-weight: 700; }
    .rl-edit { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
    .rl-in { width: 110px; }

    .opts { display: grid; gap: 8px; }
    .opts.row-opts { grid-template-columns: repeat(3, 1fr); }
    .opt { display: flex; align-items: center; gap: 8px; justify-content: center; padding: 11px 12px; border: 1px solid var(--color-border); border-radius: var(--r-md); background: var(--color-surface); cursor: pointer; transition: all .15s; font-weight: 600; font-size: var(--text-sm); }
    .opt:hover:not(:disabled) { border-color: var(--color-primary); }
    .opt.on { border-color: var(--color-primary); background: rgba(var(--color-primary-rgb),.08); color: var(--color-primary); }
    .opt va-icon { color: inherit; }

    .pay-amt { display: flex; flex-direction: column; gap: 2px; align-items: center; padding: 18px; background: var(--color-surface-alt); border-radius: var(--r-md); margin-bottom: 16px; }
    .pa-val { font-family: var(--font-display); font-size: 2rem; font-weight: 800; }
    .inv-head { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
    .inv-lines th, .inv-lines td { padding: 9px 0; }
    .range { width: 100%; accent-color: var(--color-primary); }

    @media (max-width: 1000px) { .kpis { grid-template-columns: 1fr 1fr; } .usage-grid, .credits-grid, .rls { grid-template-columns: 1fr; } .opts.row-opts { grid-template-columns: 1fr; } }
  `],
})
export class BillingComponent {
  store = inject(BillingStore);
  private auth = inject(AuthService);
  private toast = inject(ToastService);

  tab = signal<Tab>('usage');
  money = inr; num = compact; date = fmtDate;
  inst = computed(() => this.auth.institution().name);
  due = (iso: string) => relFuture(iso + 'T00:00:00');
  absv = Math.abs;

  metrics = METRICS;
  channels: AlertChannel[] = ['inapp', 'email', 'sms'];
  quickAmounts = [25000, 50000, 100000, 250000];
  methods = ['UPI', 'Card', 'Net-banking'];

  disc = computed(() => Math.round(this.store.cycleOverageINR() * (this.store.discount.pct / 100)));

  bandOf(r: MeterRow) { return r.loadPct >= 100 ? 'red' : r.loadPct >= 80 ? 'amber' : ''; }
  capW(pct: number) { return Math.min(100, pct); }
  loadPct(cur: number, max: number) { return max > 0 ? Math.round((cur / max) * 100) : 0; }
  metricIcon(m: MetricKey) { return METRIC_BY_KEY[m].icon; }
  metricLabel(m: MetricKey) { return METRIC_BY_KEY[m].label; }
  channelText(cs: AlertChannel[]) { return cs.map(c => CHANNEL_LABEL[c]).join(', '); }
  chLabel(c: AlertChannel) { return CHANNEL_LABEL[c]; }
  chIcon(c: AlertChannel) { return c === 'email' ? 'mail' : c === 'sms' ? 'message-square' : 'bell'; }
  scopeIcon(s: string) { return s === 'api' ? 'plug' : s === 'voice' ? 'phone' : s === 'whatsapp' ? 'message-circle' : 'mail'; }
  methodIcon(m: string) { return m === 'UPI' ? 'smartphone' : m === 'Card' ? 'credit-card' : 'building'; }

  // ---- invoice / pay ----
  invoiceItem = signal<Invoice | null>(null);
  payItem = signal<Invoice | null>(null);
  payMethod = signal('UPI');
  openInvoice(i: Invoice) { this.invoiceItem.set(i); }
  openPay(i: Invoice) { this.invoiceItem.set(null); this.payItem.set(i); }
  confirmPay() {
    const i = this.payItem();
    if (!i) return;
    this.store.payInvoice(i.id, 'Razorpay · ' + this.payMethod(), TODAY);
    this.payItem.set(null);
    this.toast.success(`Payment of ${inr(i.totalINR)} received — ${i.period} invoice marked paid.`);
  }
  downloadNote() { this.toast.info('Invoice PDF download started.'); }

  // ---- credits / top-up ----
  topupOpen = signal(false);
  topupAmt = signal(50000);
  topupTax = computed(() => Math.round(this.topupAmt() * (this.store.tax.gstPercent / 100)));
  openTopup(a: number) { this.topupAmt.set(a || 50000); this.topupOpen.set(true); }
  confirmTopup() {
    const amt = this.topupAmt();
    if (amt <= 0) return;
    this.store.loadCredits(amt, 'Razorpay · ' + this.payMethod(), TODAY);
    this.topupOpen.set(false);
    this.toast.success(`${inr(amt)} in credits added — new balance ${inr(this.store.creditBalance())}.`);
  }

  // ---- alerts ----
  alertOpen = signal(false);
  editingAlert = signal<string | null>(null);
  alMetric = signal<MetricKey>('voice.pulses');
  alThreshold = signal(80);
  alChannels = signal<AlertChannel[]>(['inapp', 'email']);
  openAlert(id?: string) {
    if (id) {
      const a = this.store.alerts().find(x => x.id === id);
      if (a) { this.editingAlert.set(id); this.alMetric.set(a.metric); this.alThreshold.set(a.thresholdPct); this.alChannels.set([...a.channels]); }
    } else {
      this.editingAlert.set(null); this.alMetric.set('voice.pulses'); this.alThreshold.set(80); this.alChannels.set(['inapp', 'email']);
    }
    this.alertOpen.set(true);
  }
  toggleCh(c: AlertChannel) {
    this.alChannels.update(cs => cs.includes(c) ? cs.filter(x => x !== c) : [...cs, c]);
  }
  saveAlert() {
    const ch = this.alChannels().length ? this.alChannels() : ['inapp' as AlertChannel];
    const id = this.editingAlert();
    if (id) { this.store.updateAlert(id, { metric: this.alMetric(), thresholdPct: this.alThreshold(), channels: ch }); this.toast.success('Alert updated.'); }
    else { this.store.addAlert(this.alMetric(), this.alThreshold(), ch); this.toast.success('Usage alert added.'); }
    this.alertOpen.set(false);
  }

  saveLimit(id: string, val: string) {
    const n = Math.max(1, +val || 1);
    this.store.updateRateLimit(id, n);
    this.toast.success('Rate limit updated.');
  }
}
