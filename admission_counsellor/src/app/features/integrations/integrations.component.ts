import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { IconComponent } from '../../shared/ui/icon.component';
import { PageHeaderComponent } from '../../shared/ui/layout.component';
import { DrawerComponent } from '../../shared/ui/drawer.component';
import { ToastService } from '../../core/toast.service';
import { CounselorService } from '../../core/counselor.service';
import { relTime } from '../../shared/util/format';

type IntStatus = 'connected' | 'degraded' | 'error' | 'disconnected';
interface FieldMap { id: string; source: string; target: string; transform: string; }
interface RetryEvent { id: string; ts: string; event: string; status: 'success' | 'failed' | 'retried'; attempts: number; detail: string; }
interface Integration {
  id: string; name: string; vendor: string; category: string; icon: string; hue: string;
  status: IntStatus; lastSync?: string; frequency: string; apiHealth: number; errorCount: number;
  account?: string; scope: string; mappings: FieldMap[]; logs: RetryEvent[];
}
interface CatalogItem { id: string; name: string; vendor: string; category: string; icon: string; hue: string; scope: string; }

@Component({
  selector: 'va-integrations',
  standalone: true,
  imports: [IconComponent, PageHeaderComponent, DrawerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
<div class="page page-grid">
  <va-page-header
    title="Integrations & API Management"
    [subtitle]="subtitle()">
    <span class="chip vault-chip" title="Credentials are encrypted and stored server-side">
      <va-icon name="lock" [size]="12"></va-icon> Secrets in vault
    </span>
    <button class="btn btn-ghost" (click)="testAll()"><va-icon name="zap" [size]="16"></va-icon><span class="hide-xs">Test all</span></button>
    <button class="btn btn-primary" (click)="openMarketplace()"><va-icon name="plus" [size]="16"></va-icon>Add integration</button>
  </va-page-header>

  <!-- Health summary -->
  <section class="summary">
    <div class="sum-card" [attr.data-tone]="'connected'"><span class="sum-ic"><va-icon name="check-circle" [size]="18"></va-icon></span><div class="sum-text"><span class="t-h3 t-num">{{ counts().connected }}</span><span class="t-cap t-muted">Connected</span></div></div>
    <div class="sum-card" [attr.data-tone]="'degraded'"><span class="sum-ic"><va-icon name="alert-triangle" [size]="18"></va-icon></span><div class="sum-text"><span class="t-h3 t-num">{{ counts().degraded }}</span><span class="t-cap t-muted">Degraded</span></div></div>
    <div class="sum-card" [attr.data-tone]="'error'"><span class="sum-ic"><va-icon name="alert-circle" [size]="18"></va-icon></span><div class="sum-text"><span class="t-h3 t-num">{{ counts().error }}</span><span class="t-cap t-muted">Errors</span></div></div>
    <div class="sum-card" [attr.data-tone]="'disconnected'"><span class="sum-ic"><va-icon name="plug" [size]="18"></va-icon></span><div class="sum-text"><span class="t-h3 t-num">{{ counts().disconnected }}</span><span class="t-cap t-muted">Not connected</span></div></div>
    <div class="sum-vault"><va-icon name="shield-check" [size]="18"></va-icon><div><div class="t-sm" style="font-weight:600">Vaulted credentials</div><p class="t-cap t-muted">API keys and OAuth tokens are stored in an encrypted vault — never in the browser or client bundle.</p></div></div>
  </section>

  <!-- Filter chips -->
  <div class="filters">
    @for (f of filterDefs; track f.key) {
      <button class="seg-chip" [class.active]="filter() === f.key" (click)="filter.set(f.key)">{{ f.label }}<span class="cnt t-num">{{ countFor(f.key) }}</span></button>
    }
  </div>

  @if (visible().length) {
    <section class="grid">
      @for (it of visible(); track it.id) {
        <article class="int-card" [attr.data-status]="it.status">
          <header class="ic-head">
            <span class="logo" [style.--brand]="it.hue"><va-icon [name]="it.icon" [size]="20"></va-icon></span>
            <div class="ic-id"><div class="ic-name truncate">{{ it.name }}</div><div class="t-cap t-muted truncate">{{ it.vendor }} · {{ it.category }}</div></div>
            <span class="pill" [attr.data-status]="it.status"><span class="dt"></span>{{ statusLabel(it.status) }}</span>
          </header>
          <p class="ic-scope t-sm t-muted">{{ it.scope }}</p>
          <div class="ic-health">
            <div class="hl-row between"><span class="t-cap t-muted">API health</span><span class="t-cap t-num" [attr.data-tone]="healthTone(it)">{{ healthDisplay(it) }}</span></div>
            <div class="hl-track"><span class="hl-fill" [attr.data-tone]="healthTone(it)" [style.width.%]="it.status === 'disconnected' ? 0 : it.apiHealth"></span></div>
          </div>
          <dl class="ic-meta">
            <dt>Last sync</dt><dd class="t-num">{{ it.lastSync ? relTime(it.lastSync) : '—' }}</dd>
            <dt>Frequency</dt><dd>{{ it.frequency }}</dd>
            <dt>Errors (24h)</dt><dd class="t-num" [class.err]="it.errorCount > 0">{{ it.errorCount }}</dd>
            <dt>Field maps</dt><dd class="t-num">{{ it.mappings.length }}</dd>
          </dl>
          <footer class="ic-actions">
            <div class="ic-links">
              <button class="lnk" (click)="openMapping(it)"><va-icon name="git-branch" [size]="14"></va-icon>Field mapping</button>
              <button class="lnk" (click)="openLogs(it)"><va-icon name="scroll-text" [size]="14"></va-icon>Retry logs</button>
            </div>
            <div class="ic-cta">
              @if (it.status === 'disconnected') {
                <button class="btn btn-sm btn-primary btn-block" (click)="openConnect(it)"><va-icon name="plug" [size]="14"></va-icon>Connect</button>
              } @else {
                <button class="btn btn-sm btn-ghost grow" (click)="test(it)"><va-icon name="zap" [size]="14"></va-icon>Test</button>
                @if (it.status === 'error' || it.status === 'degraded') { <button class="btn btn-sm btn-subtle grow" (click)="reconnect(it)"><va-icon name="refresh" [size]="14"></va-icon>Reconnect</button> }
                <button class="btn btn-sm btn-ghost btn-icon" title="Disconnect" (click)="disconnect(it)"><va-icon name="x" [size]="14"></va-icon></button>
              }
            </div>
          </footer>
        </article>
      }
    </section>
  } @else {
    <div class="empty"><div class="ill"><va-icon name="plug" [size]="28"></va-icon></div><div class="t-h4">No integrations in this view</div><p class="t-sm t-muted">Try a different filter, or add a new integration to extend what {{ meta().name }} can act on.</p><button class="btn btn-primary" (click)="filter.set('all')"><va-icon name="layers" [size]="16"></va-icon>Show all</button></div>
  }

  <p class="footnote t-cap t-muted"><va-icon name="lock" [size]="13"></va-icon> Credentials for every integration are encrypted at rest in a secrets vault and injected server-side at call time. They are never exposed to the client.</p>
</div>

<!-- Marketplace modal -->
@if (marketOpen()) {
  <div class="scrim" (click)="marketOpen.set(false)">
    <div class="modal market" (click)="$event.stopPropagation()">
      <div class="m-head"><div><div class="t-h3">Add an integration</div><div class="t-cap t-muted">Pick a provider to connect. Keys are sealed in the vault.</div></div><button class="x" (click)="marketOpen.set(false)"><va-icon name="x" [size]="18"></va-icon></button></div>
      <div class="market-grid">
        @for (c of catalog(); track c.id) {
          <button class="mk-card" (click)="pickProvider(c)">
            <span class="logo" [style.--brand]="c.hue"><va-icon [name]="c.icon" [size]="18"></va-icon></span>
            <div class="mk-id"><div class="mk-name">{{ c.name }}</div><div class="t-cap t-muted">{{ c.vendor }} · {{ c.category }}</div></div>
            <va-icon name="plus" [size]="16" class="mk-add"></va-icon>
          </button>
        } @empty { <div class="t-sm t-muted" style="padding:18px">Every available provider is already added.</div> }
      </div>
    </div>
  </div>
}

<!-- Connect modal (credentials) -->
@if (connectItem(); as it) {
  <div class="scrim" (click)="connectItem.set(null)">
    <div class="modal connect" (click)="$event.stopPropagation()">
      <div class="m-head"><div class="row gap-2"><span class="logo" [style.--brand]="it.hue"><va-icon [name]="it.icon" [size]="18"></va-icon></span><div><div class="t-h4">Connect {{ it.name }}</div><div class="t-cap t-muted">{{ it.vendor }} · {{ it.category }}</div></div></div><button class="x" (click)="connectItem.set(null)"><va-icon name="x" [size]="18"></va-icon></button></div>
      <div class="field"><span class="lbl">Account / workspace</span><input class="in" [value]="cAccount()" (input)="cAccount.set($any($event.target).value)" placeholder="e.g. acme.my.salesforce.com"></div>
      <div class="field"><span class="lbl">{{ it.category === 'CRM' || it.category === 'Student Information' ? 'API key / client secret' : 'API key / auth token' }}</span><input class="in" type="password" [value]="cKey()" (input)="cKey.set($any($event.target).value)" placeholder="••••••••••••••••"></div>
      <div class="field"><span class="lbl">Sync frequency</span>
        <select class="in" (change)="cFreq.set($any($event.target).value)">
          @for (f of freqs; track f) { <option [value]="f" [selected]="f === cFreq()">{{ f }}</option> }
        </select>
      </div>
      <div class="banner info"><va-icon name="lock" [size]="15"></va-icon><span>Credentials are sent over TLS and stored in the vault — never in the browser.</span></div>
      <button class="btn btn-primary btn-block" [disabled]="!cAccount()" (click)="confirmConnect(it)"><va-icon name="plug" [size]="16"></va-icon> Connect & verify</button>
    </div>
  </div>
}

<!-- Field-mapping drawer -->
<va-drawer [open]="!!mapItem()" [title]="mapItem() ? ('Field mapping · ' + mapItem()!.name) : ''" subtitle="Map provider fields to the candidate/student record" [width]="560" (close)="closeMapping()">
  @if (mapItem(); as it) {
    <div class="map-intro banner info"><va-icon name="git-branch" [size]="16"></va-icon><span>{{ it.name }} fields on the left flow into {{ meta().short }} attributes on the right. {{ meta().name }} reads these — but only ever answers from approved knowledge.</span></div>
    <div class="map-head"><span>Source field</span><span></span><span>{{ meta().short }} attribute</span><span>Transform</span><span></span></div>
    <div class="map-rows">
      @for (m of mapDraft(); track m.id) {
        <div class="map-row">
          <input class="in" [value]="m.source" (input)="editMap(m.id, { source: $any($event.target).value })" placeholder="source.field">
          <va-icon name="arrow-right" [size]="14" class="arrow"></va-icon>
          <select class="in" (change)="editMap(m.id, { target: $any($event.target).value })">
            @for (t of targets(); track t) { <option [value]="t" [selected]="t === m.target">{{ t }}</option> }
          </select>
          <select class="in tf" (change)="editMap(m.id, { transform: $any($event.target).value })">
            @for (t of transforms; track t) { <option [value]="t" [selected]="t === m.transform">{{ t }}</option> }
          </select>
          <button class="x sm" (click)="removeMap(m.id)" title="Remove"><va-icon name="trash" [size]="14"></va-icon></button>
        </div>
      } @empty { <div class="t-sm t-muted map-empty">No field mappings yet. Add one to start moving data.</div> }
    </div>
    <button class="btn btn-ghost btn-sm add-map" (click)="addMap()"><va-icon name="plus" [size]="14"></va-icon> Add mapping</button>
  }
  <div footer>
    <button class="btn btn-ghost grow" (click)="closeMapping()">Cancel</button>
    <button class="btn btn-primary grow" (click)="saveMapping()"><va-icon name="check" [size]="16"></va-icon> Save mapping</button>
  </div>
</va-drawer>

<!-- Retry logs drawer -->
<va-drawer [open]="!!logItem()" [title]="logItem() ? ('Retry logs · ' + logItem()!.name) : ''" subtitle="Recent sync events and the retry queue" [width]="520" (close)="logItem.set(null)">
  @if (logItem(); as it) {
    @if (it.logs.length) {
      <div class="logs">
        @for (l of it.logs; track l.id) {
          <div class="log" [attr.data-s]="l.status">
            <span class="log-ic"><va-icon [name]="l.status === 'failed' ? 'alert-circle' : l.status === 'retried' ? 'refresh' : 'check-circle'" [size]="15"></va-icon></span>
            <div class="log-body"><div class="between"><span class="log-ev">{{ l.event }}</span><span class="t-cap t-muted">{{ relTime(l.ts) }}</span></div><div class="t-cap t-muted">{{ l.detail }} · {{ l.attempts }} attempt(s)</div></div>
            @if (l.status === 'failed') { <button class="btn btn-sm btn-subtle" (click)="retryEvent(it, l)">Retry</button> }
          </div>
        }
      </div>
    } @else { <div class="t-sm t-muted" style="padding:16px">No failed events — the retry queue is empty.</div> }
  }
</va-drawer>`,
  styles: [`
:host { display: block; }
.summary { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)) minmax(0, 1.6fr); gap: 14px; }
.sum-card { display: flex; align-items: center; gap: 12px; padding: 16px; border-radius: var(--r-lg); background: var(--color-surface); border: 1px solid var(--color-border); box-shadow: var(--e1); }
.sum-ic { width: 40px; height: 40px; border-radius: var(--r-md); display: grid; place-items: center; flex: none; }
.sum-text { display: flex; flex-direction: column; gap: 1px; }
.sum-card[data-tone='connected'] .sum-ic { background: var(--color-success-soft); color: var(--color-success); }
.sum-card[data-tone='degraded'] .sum-ic { background: var(--color-warning-soft); color: var(--color-warning); }
.sum-card[data-tone='error'] .sum-ic { background: var(--color-danger-soft); color: var(--color-danger); }
.sum-card[data-tone='disconnected'] .sum-ic { background: var(--color-surface-alt); color: var(--color-text-muted); }
.sum-vault { display: flex; align-items: center; gap: 12px; padding: 14px 16px; border-radius: var(--r-lg); background: rgba(var(--color-accent-2-rgb), .07); border: 1px solid rgba(var(--color-accent-2-rgb), .22); }
.sum-vault va-icon { color: var(--color-accent-2); flex: none; } .sum-vault p { margin: 2px 0 0; max-width: 42ch; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; }
.seg-chip { display: inline-flex; align-items: center; gap: 7px; padding: 7px 13px; border-radius: var(--r-pill); font-size: var(--text-sm); font-weight: 600; color: var(--color-text-muted); background: var(--color-surface); border: 1px solid var(--color-border); transition: all .15s; }
.seg-chip:hover { background: var(--color-surface-alt); color: var(--color-text); }
.seg-chip.active { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
.seg-chip .cnt { font-size: 11px; padding: 1px 7px; border-radius: var(--r-pill); background: var(--color-surface-alt); color: var(--color-text-muted); }
.seg-chip.active .cnt { background: rgba(255,255,255,.22); color: #fff; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: 16px; }
.int-card { display: flex; flex-direction: column; gap: 13px; padding: 18px; border-radius: var(--r-lg); background: var(--color-surface); border: 1px solid var(--color-border); box-shadow: var(--e1); position: relative; overflow: hidden; transition: box-shadow .18s ease, border-color .18s ease, transform .18s ease; }
.int-card::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: transparent; }
.int-card:hover { box-shadow: var(--e2); transform: translateY(-2px); }
.int-card[data-status='connected']::before { background: var(--color-success); }
.int-card[data-status='degraded']::before { background: var(--color-warning); }
.int-card[data-status='error']::before { background: var(--color-danger); }
.int-card[data-status='disconnected']::before { background: var(--color-border-strong); }
.ic-head { display: flex; align-items: center; gap: 12px; }
.logo { width: 42px; height: 42px; border-radius: var(--r-md); display: grid; place-items: center; flex: none; color: var(--brand, var(--color-primary)); background: color-mix(in srgb, var(--brand, var(--color-primary)) 12%, var(--color-surface)); border: 1px solid color-mix(in srgb, var(--brand, var(--color-primary)) 26%, var(--color-border)); }
.ic-id { min-width: 0; flex: 1; } .ic-name { font-size: var(--text-h4); font-weight: 600; line-height: 1.2; }
.pill { display: inline-flex; align-items: center; gap: 6px; flex: none; white-space: nowrap; font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: var(--r-pill); border: 1px solid transparent; }
.pill .dt { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.pill[data-status='connected'] { background: var(--color-success-soft); color: var(--color-success); } .pill[data-status='connected'] .dt { background: var(--color-success); box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-success) 22%, transparent); }
.pill[data-status='degraded'] { background: var(--color-warning-soft); color: var(--color-warning); } .pill[data-status='degraded'] .dt { background: var(--color-warning); }
.pill[data-status='error'] { background: var(--color-danger-soft); color: var(--color-danger); } .pill[data-status='error'] .dt { background: var(--color-danger); }
.pill[data-status='disconnected'] { background: var(--color-surface-alt); color: var(--color-text-muted); } .pill[data-status='disconnected'] .dt { background: var(--color-text-muted); }
.ic-scope { margin: 0; min-height: 38px; }
.ic-health { display: flex; flex-direction: column; gap: 6px; }
.hl-track { height: 6px; border-radius: 999px; background: var(--color-surface-alt); overflow: hidden; }
.hl-fill { display: block; height: 100%; border-radius: 999px; background: var(--color-success); transition: width .5s ease; }
.hl-fill[data-tone='med'] { background: var(--color-warning); } .hl-fill[data-tone='low'] { background: var(--color-danger); } .hl-fill[data-tone='none'] { background: var(--color-border-strong); }
.hl-row span[data-tone='high'] { color: var(--color-success); font-weight: 700; } .hl-row span[data-tone='med'] { color: var(--color-warning); font-weight: 700; } .hl-row span[data-tone='low'] { color: var(--color-danger); font-weight: 700; } .hl-row span[data-tone='none'] { color: var(--color-text-muted); }
.ic-meta { display: grid; grid-template-columns: auto 1fr auto 1fr; gap: 9px 12px; font-size: var(--text-sm); align-items: baseline; }
.ic-meta dt { color: var(--color-text-muted); font-size: var(--text-cap); white-space: nowrap; } .ic-meta dd { margin: 0; font-weight: 600; text-align: right; min-width: 0; } .ic-meta dd.err { color: var(--color-danger); }
.ic-actions { display: flex; flex-direction: column; gap: 10px; margin-top: auto; padding-top: 12px; border-top: 1px solid var(--color-border); }
.ic-links { display: flex; gap: 6px; }
.lnk { display: inline-flex; align-items: center; gap: 5px; flex: 1; justify-content: center; background: transparent; border: 1px solid var(--color-border); border-radius: var(--r-md); padding: 7px 8px; font-size: var(--text-cap); font-weight: 600; color: var(--color-text-muted); transition: all .15s; }
.lnk:hover { background: var(--color-surface-alt); color: var(--color-text); }
.ic-cta { display: flex; gap: 6px; } .ic-cta .grow { flex: 1; }
.empty { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 10px; padding: 56px 24px; background: var(--color-surface); border: 1px dashed var(--color-border-strong); border-radius: var(--r-lg); }
.empty .ill { width: 64px; height: 64px; border-radius: 18px; display: grid; place-items: center; background: var(--color-surface-alt); color: var(--color-text-muted); margin-bottom: 4px; } .empty p { max-width: 380px; margin: 0; } .empty .btn { margin-top: 6px; }
.vault-chip { background: rgba(var(--color-accent-2-rgb), .10); color: var(--color-accent-2); border-color: rgba(var(--color-accent-2-rgb), .25); }
.footnote { display: flex; align-items: center; gap: 7px; } .footnote va-icon { flex: none; }

/* Modals */
.scrim { position: fixed; inset: 0; background: rgba(2,6,23,.5); backdrop-filter: blur(3px); z-index: 80; display: grid; place-items: center; padding: 20px; animation: va-fade-up .15s ease; }
.modal { width: min(560px, 94vw); background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-lg); box-shadow: var(--e3); padding: 20px; max-height: 86vh; overflow-y: auto; }
.modal.connect { width: min(440px, 94vw); }
.m-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.x { background: none; border: none; color: var(--color-text-muted); padding: 4px; border-radius: 6px; display: inline-flex; } .x:hover { background: var(--color-surface-alt); color: var(--color-text); }
.x.sm { padding: 6px; flex: none; }
.market-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.mk-card { display: flex; align-items: center; gap: 11px; padding: 12px; border: 1px solid var(--color-border); border-radius: var(--r-md); background: var(--color-surface); text-align: left; transition: all .15s; }
.mk-card:hover { border-color: var(--color-primary); background: var(--color-surface-alt); }
.mk-card .logo { width: 36px; height: 36px; } .mk-id { min-width: 0; flex: 1; } .mk-name { font-weight: 600; font-size: var(--text-sm); } .mk-add { color: var(--color-primary); }
.field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 13px; } .field .lbl { font-size: var(--text-cap); color: var(--color-text-muted); font-weight: 600; }
.in { width: 100%; padding: 9px 11px; border: 1px solid var(--color-border); border-radius: var(--r-md); background: var(--color-surface); color: var(--color-text); font: inherit; font-size: var(--text-sm); }
.in:focus { outline: none; border-color: var(--color-accent); box-shadow: var(--ring); }
.btn-block { width: 100%; justify-content: center; margin-top: 6px; }

/* Mapping drawer */
.map-intro { margin-bottom: 14px; }
.map-head { display: grid; grid-template-columns: 1fr 20px 1fr 110px 30px; gap: 8px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: var(--color-text-muted); padding: 0 2px 6px; }
.map-rows { display: flex; flex-direction: column; gap: 8px; }
.map-row { display: grid; grid-template-columns: 1fr 20px 1fr 110px 30px; gap: 8px; align-items: center; }
.map-row .arrow { color: var(--color-text-muted); }
.map-row .in { padding: 7px 9px; } .map-row .tf { font-size: var(--text-cap); }
.map-empty { padding: 16px; text-align: center; }
.add-map { margin-top: 12px; }

/* Logs drawer */
.logs { display: flex; flex-direction: column; gap: 8px; }
.log { display: flex; align-items: center; gap: 10px; padding: 11px 12px; border: 1px solid var(--color-border); border-radius: var(--r-md); }
.log-ic { flex: none; } .log[data-s='failed'] .log-ic { color: var(--color-danger); } .log[data-s='retried'] .log-ic { color: var(--color-warning); } .log[data-s='success'] .log-ic { color: var(--color-success); }
.log-body { flex: 1; min-width: 0; } .log-ev { font-size: var(--text-sm); font-weight: 600; }

@media (max-width: 1100px) { .summary { grid-template-columns: repeat(2, 1fr); } .sum-vault { grid-column: 1 / -1; } }
@media (max-width: 640px) { .summary { grid-template-columns: 1fr 1fr; } .hide-xs { display: none; } .market-grid { grid-template-columns: 1fr; } }
`],
})
export class IntegrationsComponent {
  private toast = inject(ToastService);
  counselor = inject(CounselorService);
  meta = this.counselor.activeMeta;
  relTime = relTime;
  subtitle = computed(() => `Connect telephony, messaging, calendar, payments and CRM/SIS systems. ${this.meta().name} draws context from connected systems but only ever speaks from approved knowledge.`);

  filter = signal<'all' | IntStatus>('all');
  filterDefs: { key: 'all' | IntStatus; label: string }[] = [
    { key: 'all', label: 'All' }, { key: 'connected', label: 'Connected' }, { key: 'degraded', label: 'Degraded' }, { key: 'error', label: 'Errors' }, { key: 'disconnected', label: 'Not connected' },
  ];

  private log = (id: string, ev: string, status: RetryEvent['status'], attempts: number, detail: string, h: number): RetryEvent =>
    ({ id, ts: `2026-06-16T0${h}:0${attempts}:00`, event: ev, status, attempts, detail });

  integrations = signal<Integration[]>([
    { id: 'twilio', name: 'Twilio', vendor: 'Twilio', category: 'Telephony', icon: 'phone', hue: 'var(--ch-voice)', status: 'connected', lastSync: '2026-06-16T09:24:00', frequency: 'Real-time', apiHealth: 99, errorCount: 0, account: 'AC1f…northgate-voice', scope: 'Outbound/inbound voice calls, recordings and IVR for voice counseling.',
      mappings: [{ id: 'm1', source: 'call.from', target: 'Mobile', transform: 'E.164 phone' }, { id: 'm2', source: 'call.recording_url', target: 'Call recording', transform: 'None' }], logs: [] },
    { id: 'whatsapp', name: 'WhatsApp Business API', vendor: 'Meta', category: 'Messaging', icon: 'message-circle', hue: 'var(--ch-whatsapp)', status: 'connected', lastSync: '2026-06-16T09:28:00', frequency: 'Real-time', apiHealth: 97, errorCount: 1, account: '+91 91234 00126', scope: 'Two-way WhatsApp conversations, templates and delivery receipts.',
      mappings: [{ id: 'm1', source: 'contact.wa_id', target: 'WhatsApp', transform: 'E.164 phone' }], logs: [this.log('w1', 'template.send', 'failed', 2, 'Template not approved in this language', 8)] },
    { id: 'sendgrid', name: 'SendGrid', vendor: 'Twilio SendGrid', category: 'Email', icon: 'mail', hue: 'var(--ch-email)', status: 'connected', lastSync: '2026-06-16T08:55:00', frequency: 'Every 5 min', apiHealth: 96, errorCount: 2, account: 'admissions@northgate.edu', scope: 'Transactional email — follow-ups, document links and reminders.',
      mappings: [{ id: 'm1', source: 'event.email', target: 'Email', transform: 'Lowercase' }], logs: [this.log('s1', 'email.bounce', 'failed', 1, 'Hard bounce — invalid mailbox', 7)] },
    { id: 'gcal', name: 'Google Calendar', vendor: 'Google Workspace', category: 'Calendar', icon: 'calendar', hue: '#EA4335', status: 'connected', lastSync: '2026-06-16T09:10:00', frequency: 'Every 15 min', apiHealth: 100, errorCount: 0, account: 'counseling@northgate.edu', scope: 'V-Con and counselor availability sync for scheduling and reminders.', mappings: [], logs: [] },
    { id: 'livekit', name: 'LiveKit (WebRTC)', vendor: 'LiveKit', category: 'Video', icon: 'video', hue: 'var(--ch-vcon)', status: 'degraded', lastSync: '2026-06-16T09:18:00', frequency: 'Real-time', apiHealth: 64, errorCount: 6, account: 'rtc.northgate.counsellor', scope: 'Real-time video V-Cons between candidates, parents and the counselor.',
      mappings: [], logs: [this.log('l1', 'room.connect', 'retried', 3, 'TURN relay latency elevated', 9)] },
    { id: 'salesforce', name: 'Salesforce CRM', vendor: 'Salesforce', category: 'CRM', icon: 'building', hue: '#00A1E0', status: 'error', lastSync: '2026-06-15T22:40:00', frequency: 'Every 30 min', apiHealth: 28, errorCount: 41, account: 'northgate.my.salesforce.com', scope: 'Bi-directional lead, opportunity and stage sync with the institution CRM.',
      mappings: [{ id: 'm1', source: 'Lead.FirstName', target: 'Candidate name', transform: 'Trim' }, { id: 'm2', source: 'Lead.Email', target: 'Email', transform: 'Lowercase' }, { id: 'm3', source: 'Lead.Status', target: 'Stage', transform: 'Map values' }],
      logs: [this.log('sf1', 'lead.upsert', 'failed', 5, 'Authentication rejected (401) — token expired', 6), this.log('sf2', 'stage.sync', 'failed', 5, 'INVALID_FIELD: Status', 5)] },
    { id: 'mscal', name: 'Microsoft Calendar', vendor: 'Microsoft 365', category: 'Calendar', icon: 'calendar', hue: '#0078D4', status: 'disconnected', frequency: '—', apiHealth: 0, errorCount: 0, scope: 'Optional Outlook/Teams calendar sync for counselors on Microsoft 365.', mappings: [], logs: [] },
    { id: 'razorpay', name: 'Razorpay', vendor: 'Razorpay', category: 'Payments', icon: 'dollar-sign', hue: '#0C2451', status: 'disconnected', frequency: '—', apiHealth: 0, errorCount: 0, scope: 'Application-fee collection and payment-status webhooks (₹).', mappings: [], logs: [] },
    { id: 'erp', name: 'University ERP / SIS', vendor: 'Northgate IT', category: 'Student Information', icon: 'layers', hue: 'var(--color-primary)', status: 'disconnected', frequency: '—', apiHealth: 0, errorCount: 0, scope: 'Student records, admitted-roster export and seat reconciliation.', mappings: [], logs: [] },
  ]);

  private CATALOG: CatalogItem[] = [
    { id: 'exotel', name: 'Exotel', vendor: 'Exotel', category: 'Telephony', icon: 'phone', hue: '#3DB39E', scope: 'India cloud telephony — calls, IVR and call masking.' },
    { id: 'gupshup', name: 'Gupshup', vendor: 'Gupshup', category: 'Messaging', icon: 'message-circle', hue: '#FF6B35', scope: 'WhatsApp + SMS conversational messaging.' },
    { id: 'zoom', name: 'Zoom', vendor: 'Zoom', category: 'Video', icon: 'video', hue: '#2D8CFF', scope: 'Video V-Cons and webinar hosting.' },
    { id: 'teams', name: 'Microsoft Teams', vendor: 'Microsoft 365', category: 'Video', icon: 'video', hue: '#6264A7', scope: 'Teams meetings for counselor V-Cons.' },
    { id: 'zoho', name: 'Zoho CRM', vendor: 'Zoho', category: 'CRM', icon: 'building', hue: '#E42527', scope: 'Lead & deal sync with Zoho CRM.' },
    { id: 'hubspot', name: 'HubSpot', vendor: 'HubSpot', category: 'CRM', icon: 'building', hue: '#FF7A59', scope: 'Contact & pipeline sync with HubSpot.' },
    { id: 'payu', name: 'PayU', vendor: 'PayU', category: 'Payments', icon: 'dollar-sign', hue: '#A6CE39', scope: 'Application-fee collection (₹) and webhooks.' },
    { id: 'workday', name: 'Workday SIS', vendor: 'Workday', category: 'Student Information', icon: 'layers', hue: '#F38B00', scope: 'Student information system records & rosters.' },
    { id: 'slack', name: 'Slack', vendor: 'Slack', category: 'Notifications', icon: 'message-square', hue: '#4A154B', scope: 'Escalation & alert notifications to counselor channels.' },
    { id: 'knowlarity', name: 'Knowlarity', vendor: 'Knowlarity', category: 'Telephony', icon: 'phone', hue: '#00529B', scope: 'Cloud telephony & click-to-call for India.' },
  ];
  catalog = computed(() => { const have = new Set(this.integrations().map(i => i.id)); return this.CATALOG.filter(c => !have.has(c.id)); });

  transforms = ['None', 'Trim', 'Lowercase', 'Uppercase', 'E.164 phone', 'Parse date', 'Map values'];
  freqs = ['Real-time', 'Every 5 min', 'Every 15 min', 'Every 30 min', 'Hourly', 'Daily'];
  targets = computed(() => this.counselor.active() === 'career'
    ? ['Student name', 'Mobile', 'WhatsApp', 'Email', 'Career interest', 'Pathway', 'Skill focus', 'Mentor', 'City', 'Consent', 'Source', 'Call recording', 'Stage']
    : ['Candidate name', 'Mobile', 'WhatsApp', 'Email', 'Course interest', 'Lead source', 'Stage', 'Parent name', 'City', 'Consent', 'Application ID', 'Call recording']);

  // modal state
  marketOpen = signal(false);
  connectItem = signal<Integration | null>(null);
  cAccount = signal(''); cKey = signal(''); cFreq = signal('Real-time');
  mapItem = signal<Integration | null>(null);
  mapDraft = signal<FieldMap[]>([]);
  logItem = signal<Integration | null>(null);

  counts = computed(() => { const l = this.integrations(); return { connected: l.filter(i => i.status === 'connected').length, degraded: l.filter(i => i.status === 'degraded').length, error: l.filter(i => i.status === 'error').length, disconnected: l.filter(i => i.status === 'disconnected').length }; });
  visible = computed(() => { const f = this.filter(); const l = this.integrations(); return f === 'all' ? l : l.filter(i => i.status === f); });
  countFor(key: 'all' | IntStatus): number { return key === 'all' ? this.integrations().length : this.integrations().filter(i => i.status === key).length; }
  statusLabel(s: IntStatus) { return s === 'connected' ? 'Connected' : s === 'degraded' ? 'Degraded' : s === 'error' ? 'Error' : 'Not connected'; }
  healthTone(it: Integration): 'high' | 'med' | 'low' | 'none' { if (it.status === 'disconnected') return 'none'; if (it.apiHealth >= 90) return 'high'; if (it.apiHealth >= 60) return 'med'; return 'low'; }
  healthDisplay(it: Integration) { return it.status === 'disconnected' ? 'Offline' : it.apiHealth + '%'; }

  private patch(id: string, p: Partial<Integration>) { this.integrations.update(l => l.map(i => i.id === id ? { ...i, ...p } : i)); }

  // --- Add / connect ---
  openMarketplace() { this.marketOpen.set(true); }
  pickProvider(c: CatalogItem) {
    // add as a disconnected integration, then open the connect form
    const it: Integration = { id: c.id, name: c.name, vendor: c.vendor, category: c.category, icon: c.icon, hue: c.hue, status: 'disconnected', frequency: '—', apiHealth: 0, errorCount: 0, scope: c.scope, mappings: [], logs: [] };
    this.integrations.update(l => [...l, it]);
    this.marketOpen.set(false);
    this.openConnect(it);
    this.toast.info(`${c.name} added — enter credentials to connect.`, 'plus');
  }
  openConnect(it: Integration) { this.cAccount.set(it.account ?? ''); this.cKey.set(''); this.cFreq.set('Real-time'); this.connectItem.set(it); }
  confirmConnect(it: Integration) {
    this.patch(it.id, { status: 'connected', apiHealth: 96, errorCount: 0, lastSync: '2026-06-16T09:30:00', account: this.cAccount(), frequency: this.cFreq() });
    this.connectItem.set(null);
    this.toast.success(`${it.name} connected — credentials sealed in the vault.`, 'plug');
  }
  reconnect(it: Integration) { this.patch(it.id, { status: 'connected', apiHealth: 97, errorCount: 0, lastSync: '2026-06-16T09:30:00' }); this.toast.success(`${it.name} reconnected and back to healthy.`, 'refresh'); }
  disconnect(it: Integration) { this.patch(it.id, { status: 'disconnected', apiHealth: 0, errorCount: 0, account: undefined, frequency: '—' }); this.toast.warning(`${it.name} disconnected. ${this.meta().name} will stop using its data immediately.`, 'plug'); }
  test(it: Integration) {
    if (it.status === 'error') this.toast.danger(`${it.name} test failed — authentication rejected (401). Check Retry logs.`, 'alert-circle');
    else if (it.status === 'degraded') this.toast.warning(`${it.name} test passed with elevated latency.`, 'alert-triangle');
    else this.toast.success(`${it.name} test successful — round-trip in 142 ms.`, 'check-circle');
  }
  testAll() { this.toast.info('Running connectivity tests across all integrations…', 'zap'); }

  // --- Field mapping ---
  openMapping(it: Integration) { this.mapItem.set(it); this.mapDraft.set(it.mappings.map(m => ({ ...m }))); }
  closeMapping() { this.mapItem.set(null); }
  addMap() { this.mapDraft.update(l => [...l, { id: 'fm' + (l.length + 1) + Date.now().toString(36), source: '', target: this.targets()[0], transform: 'None' }]); }
  editMap(id: string, p: Partial<FieldMap>) { this.mapDraft.update(l => l.map(m => m.id === id ? { ...m, ...p } : m)); }
  removeMap(id: string) { this.mapDraft.update(l => l.filter(m => m.id !== id)); }
  saveMapping() {
    const it = this.mapItem(); if (!it) return;
    const clean = this.mapDraft().filter(m => m.source.trim());
    this.patch(it.id, { mappings: clean });
    this.mapItem.set(null);
    this.toast.success(`${it.name}: ${clean.length} field mapping(s) saved.`, 'git-branch');
  }

  // --- Retry logs ---
  openLogs(it: Integration) { this.logItem.set(it); }
  retryEvent(it: Integration, l: RetryEvent) {
    const logs = it.logs.map(x => x.id === l.id ? { ...x, status: 'retried' as const, attempts: x.attempts + 1 } : x);
    const remainingFails = logs.filter(x => x.status === 'failed').length;
    this.patch(it.id, { logs, errorCount: remainingFails });
    this.logItem.set(this.integrations().find(i => i.id === it.id) ?? null);
    this.toast.success(`Retrying “${l.event}” for ${it.name}…`, 'refresh');
  }
}
