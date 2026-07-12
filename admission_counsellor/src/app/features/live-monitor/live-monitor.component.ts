import { ChangeDetectionStrategy, Component, OnDestroy, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { FunnelComponent } from '../../shared/ui/funnel.component';
import { SectionCardComponent, EmptyStateComponent } from '../../shared/ui/layout.component';
import { SentimentBadgeComponent } from '../../shared/ui/badges.component';
import { AvatarComponent, AiAvatarComponent } from '../../shared/ui/avatar.component';
import { DataStore } from '../../data-access/data.store';
import { AuthService } from '../../core/auth.service';
import { CounselorService } from '../../core/counselor.service';
import { ToastService } from '../../core/toast.service';
import { ActivityItem, Channel, FunnelStage, Sentiment } from '../../domain/models';
import { CHANNEL_ICON, CHANNEL_LABEL, fmtInt, relTime } from '../../shared/util/format';

interface FloorStat {
  key: string;
  label: string;
  icon: string;
  channel?: Channel;
  tone: 'voice' | 'whatsapp' | 'email' | 'vcon' | 'team' | 'queue';
  value: number;
  caption: string;
  live: boolean;
  route?: string;
}

interface LiveSession {
  id: string;
  candidateId: string;
  name: string;
  hue: number;
  course: string;
  channel: Channel;
  actor: 'ai' | 'human';
  durationSec: number;
  sentiment: Sentiment;
  aiConfidence: number;
  intent: string;
  route: string;
}

interface ChannelLoad {
  channel: Channel;
  label: string;
  active: number;
  capacity: number;
}

interface FloorAlert {
  id: string;
  kind: 'confidence' | 'escalation' | 'failed';
  candidateId?: string;
  title: string;
  detail: string;
  urgency: 'Critical' | 'High' | 'Medium' | 'Low';
  ts: string;
  distress?: boolean;
}

/**
 * Live Communication Monitor (§27, §32.30) — the real-time "admissions floor".
 * Status counters tick on an interval to feel live; a synthetic activity item is
 * prepended every few seconds. All AI sessions carry the persistent AI badge and
 * speak only from approved knowledge; low-confidence moments surface as alerts that
 * route to the human handoff queue.
 */
@Component({
  selector: 'va-live-monitor',
  standalone: true,
  imports: [
    IconComponent, FunnelComponent, SectionCardComponent, EmptyStateComponent,
    SentimentBadgeComponent, AvatarComponent, AiAvatarComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
<div class="page page-grid">
  <!-- Header -->
  <header class="lm-head">
    <div class="lm-title">
      <span class="live-pill"><span class="dot live pulse"></span> Live</span>
      <div>
        <div class="t-h2 row-title">
          Live communication monitor
          <span class="cnsl-pill" [attr.data-v]="counselor.active()"><va-ai-avatar [size]="18" [variant]="counselor.active()"></va-ai-avatar>{{ counselor.activeMeta().name }} · {{ counselor.activeMeta().short }}</span>
        </div>
        <p class="t-sm t-muted">
          {{ career() ? 'Real-time career-guidance floor' : 'Real-time admissions floor' }} — <b>{{ auth.institution().name }}</b> · {{ auth.admissionCycle() }}
          · <span class="t-num">{{ totalActive() }}</span> conversations in progress
        </p>
      </div>
    </div>
    <div class="lm-actions">
      <span class="upd t-cap t-muted"><span class="dot live"></span> Last updated {{ lastUpdated() }}</span>
      <button class="btn btn-ghost btn-sm" (click)="toggleReconnect()" [class.btn-subtle]="reconnecting()">
        <va-icon name="plug" [size]="15"></va-icon>{{ reconnecting() ? 'Simulating drop' : 'Connected' }}
      </button>
      <button class="btn btn-ghost btn-icon" title="Refresh stream" (click)="manualRefresh()"><va-icon name="refresh" [size]="16"></va-icon></button>
    </div>
  </header>

  <!-- Reconnecting banner -->
  @if (reconnecting()) {
    <div class="banner warning recon">
      <va-icon name="refresh" [size]="18" class="spin"></va-icon>
      <span class="grow">Reconnecting to the live gateway… buffered events will replay when the stream resumes. No data is lost.</span>
      <button class="btn btn-sm btn-ghost" (click)="toggleReconnect()">Resume now</button>
    </div>
  }

  <!-- Status cards -->
  <section class="stats">
    @for (s of stats(); track s.key) {
      <button class="stat" [attr.data-tone]="s.tone" [class.clickable]="!!s.route" [disabled]="!s.route" (click)="openStat(s)">
        <span class="stat-ic"><va-icon [name]="s.icon" [size]="18"></va-icon></span>
        <div class="stat-body">
          <div class="stat-top">
            <span class="stat-val t-num">{{ fmt(s.value) }}</span>
            @if (s.live) { <span class="dot live pulse" title="Streaming live"></span> }
          </div>
          <span class="stat-label">{{ s.label }}</span>
          <span class="stat-cap t-cap t-muted">{{ s.caption }}</span>
        </div>
      </button>
    }
  </section>

  <!-- Body: main + rail -->
  <div class="lm-body">
    <div class="lm-main">
      <!-- Today funnel -->
      <va-section-card title="Today" [hint]="career() ? 'Live career-guidance funnel · resets at midnight IST' : 'Live conversion funnel · resets at midnight IST'">
        <span actions class="chip live-chip"><span class="dot live pulse"></span> Streaming</span>
        <va-funnel [stages]="todayFunnel()" (stageClick)="drillStage($event)"></va-funnel>
      </va-section-card>

      <!-- Live sessions -->
      <va-section-card title="Conversations in progress" [hint]="sessions().length + ' active · click to open the console'">
        <button actions class="btn btn-sm btn-ghost" (click)="go('/app/communications')">All channels <va-icon name="arrow-up-right" [size]="14"></va-icon></button>
        @if (sessions().length) {
          <div class="sessions">
            @for (s of sessions(); track s.id) {
              <button class="sess" [attr.data-ch]="s.channel" (click)="go(s.route)">
                <span class="sess-rail" [attr.data-ch]="s.channel"></span>
                <div class="sess-person">
                  @if (s.actor === 'ai') {
                    <va-ai-avatar [size]="38" [glow]="s.aiConfidence >= 85" [variant]="counselor.active()"></va-ai-avatar>
                  } @else {
                    <va-avatar [name]="s.name" [hue]="s.hue" [size]="38"></va-avatar>
                  }
                  <div class="sess-id">
                    <div class="sess-name truncate">{{ s.name }}</div>
                    <div class="sess-course t-cap t-muted truncate">{{ s.course }}</div>
                  </div>
                </div>
                <div class="sess-meta">
                  <span class="sess-ch" [attr.data-ch]="s.channel"><va-icon [name]="chIcon(s.channel)" [size]="13"></va-icon>{{ chLabel(s.channel) }}</span>
                  <span class="chip dur"><va-icon name="clock" [size]="12"></va-icon><span class="t-num">{{ dur(s.durationSec) }}</span></span>
                  <span class="chip handler" [class.ai]="s.actor === 'ai'">{{ s.actor === 'ai' ? aiLabel() : 'Human' }}</span>
                </div>
                <div class="sess-signals">
                  <div class="sig">
                    <span class="t-cap t-muted">Live sentiment</span>
                    <va-sentiment-badge [value]="s.sentiment" [showLabel]="true"></va-sentiment-badge>
                  </div>
                  <div class="sig conf">
                    <span class="t-cap t-muted row between"><span>AI confidence</span><span class="t-num" [attr.data-band]="confBand(s.aiConfidence)">{{ s.aiConfidence }}%</span></span>
                    <span class="conf-track"><span class="conf-fill" [attr.data-band]="confBand(s.aiConfidence)" [style.width.%]="s.aiConfidence"></span></span>
                  </div>
                </div>
                <span class="sess-intent t-cap t-muted truncate"><va-icon name="zap" [size]="12"></va-icon>{{ s.intent }}</span>
              </button>
            }
          </div>
        } @else {
          <va-empty icon="headphones" title="No live conversations" [message]="'When ' + audience() + ' engage across voice, WhatsApp, email or V-Con, active sessions will appear here in real time.'"></va-empty>
        }
      </va-section-card>

      <!-- Channel load -->
      <va-section-card title="Channel load" hint="Active sessions vs. provisioned capacity">
        <div class="loads">
          @for (l of channelLoads(); track l.channel) {
            <div class="load">
              <div class="load-head">
                <span class="load-ch" [attr.data-ch]="l.channel"><va-icon [name]="chIcon(l.channel)" [size]="14"></va-icon>{{ l.label }}</span>
                <span class="t-cap t-muted"><span class="t-num">{{ l.active }}</span> / {{ l.capacity }}</span>
              </div>
              <div class="progress" [class.ai]="loadPct(l) < 75" [class.warn]="loadPct(l) >= 75">
                <span [style.width.%]="loadPct(l)"></span>
              </div>
              <span class="t-cap" [class.t-muted]="loadPct(l) < 90" [class.hot]="loadPct(l) >= 90">{{ loadPct(l) }}% utilised{{ loadPct(l) >= 90 ? ' · near capacity' : '' }}</span>
            </div>
          }
        </div>
      </va-section-card>
    </div>

    <!-- Rail -->
    <aside class="lm-rail">
      <!-- Live activity stream -->
      <va-section-card title="Live activity stream" hint="Auto-updating" [flush]="true">
        <span actions class="chip live-chip"><span class="dot live pulse"></span> Live</span>
        <div class="stream scroll-y">
          @for (a of stream(); track a.id) {
            <div class="act" [class.fresh]="a.id === newestId()">
              <span class="act-ic" [attr.data-ch]="a.channel"><va-icon [name]="chIcon(a.channel)" [size]="14"></va-icon></span>
              <div class="act-body">
                <span class="act-text">{{ a.text }}</span>
                <span class="act-meta t-cap t-muted">
                  {{ a.candidate }} · {{ relTime(a.ts) }} ·
                  <span class="ai-tag" [class.human]="a.actor !== 'ai'">{{ a.actor === 'ai' ? aiLabel() : 'Human' }}</span>
                </span>
              </div>
              @if (a.sentiment) { <va-sentiment-badge [value]="a.sentiment"></va-sentiment-badge> }
            </div>
          }
        </div>
      </va-section-card>

      <!-- Alerts -->
      <va-section-card [title]="'Alerts'" [hint]="alerts().length + ' need attention'">
        <button actions class="btn btn-sm btn-ghost" (click)="go('/app/handoff')">Handoff queue</button>
        @if (alerts().length) {
          <div class="alerts">
            @for (al of alerts(); track al.id) {
              <button class="alert" [attr.data-kind]="al.kind" [class.distress]="al.distress" (click)="openAlert(al)">
                <span class="alert-ic">
                  <va-icon [name]="alertIcon(al.kind)" [size]="15"></va-icon>
                </span>
                <div class="alert-body">
                  <div class="alert-top">
                    <span class="alert-title truncate">{{ al.title }}</span>
                    <span class="urg" [attr.data-u]="al.urgency">{{ al.urgency }}</span>
                  </div>
                  <span class="t-cap t-muted">{{ al.detail }}</span>
                  <span class="t-cap t-muted alert-ts">{{ relTime(al.ts) }} · routes to human handoff</span>
                </div>
              </button>
            }
          </div>
        } @else {
          <va-empty icon="shield-check" title="All clear" [message]="'No low-confidence moments, escalations or failed attempts right now. ' + counselor.activeMeta().name + ' is handling conversations within approved-knowledge guardrails.'"></va-empty>
        }
      </va-section-card>

      <!-- Counselor availability -->
      <va-section-card title="Counselor availability" hint="Human team on the floor">
        <div class="team">
          @for (c of counselors; track c.name) {
            <div class="member">
              <va-avatar [name]="c.name" [hue]="c.hue" [size]="32"></va-avatar>
              <div class="member-id">
                <span class="member-name truncate">{{ c.name }}</span>
                <span class="t-cap t-muted">{{ c.role }}</span>
              </div>
              <span class="presence" [attr.data-s]="c.status">
                <span class="dot" [class.live]="c.status === 'available'" [class.limited]="c.status === 'on-call'" [class.paused]="c.status === 'away'"></span>
                {{ presenceLabel(c.status) }}
              </span>
            </div>
          }
        </div>
      </va-section-card>
    </aside>
  </div>
</div>
  `,
  styles: [`
    :host { display: block; }

    .cnsl-pill{display:inline-flex;align-items:center;gap:6px;font-size:var(--text-cap);font-weight:700;padding:4px 10px 4px 5px;border-radius:var(--r-pill);background:rgba(var(--color-accent-2-rgb),.12);color:var(--color-accent-2);}
    .cnsl-pill[data-v='career']{background:rgba(var(--color-career-rgb),.14);color:var(--color-career);}
    .row-title { display: inline-flex; align-items: center; gap: 10px; flex-wrap: wrap; }

    /* Header */
    .lm-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .lm-title { display: flex; align-items: flex-start; gap: 14px; }
    .lm-title p { margin-top: 4px; }
    .live-pill { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 700;
      color: var(--color-success); background: var(--color-success-soft); padding: 6px 11px; border-radius: var(--r-pill);
      text-transform: uppercase; letter-spacing: .05em; margin-top: 2px; flex: none; }
    .lm-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .upd { display: inline-flex; align-items: center; gap: 6px; }

    .recon { align-items: center; }
    .recon va-icon { color: var(--color-warning); flex: none; }

    /* Status cards */
    .stats { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; }
    .stat { position: relative; display: flex; align-items: flex-start; gap: 12px; text-align: left; padding: 16px;
      background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--r-lg); box-shadow: var(--e1);
      overflow: hidden; transition: transform .12s ease, box-shadow .15s ease, border-color .15s ease; }
    .stat::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; opacity: .85; }
    .stat[data-tone='voice']::before { background: var(--ch-voice); }
    .stat[data-tone='whatsapp']::before { background: var(--ch-whatsapp); }
    .stat[data-tone='email']::before { background: var(--ch-email); }
    .stat[data-tone='vcon']::before { background: var(--ch-vcon); }
    .stat[data-tone='team']::before { background: var(--color-primary); }
    .stat[data-tone='queue']::before { background: var(--color-warning); }
    .stat.clickable { cursor: pointer; }
    .stat.clickable:hover { transform: translateY(-2px); box-shadow: var(--e2); border-color: var(--color-border-strong); }
    .stat:disabled { cursor: default; }
    .stat-ic { width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; flex: none;
      background: var(--color-surface-alt); color: var(--color-text-muted); }
    .stat[data-tone='voice'] .stat-ic { color: var(--ch-voice); background: color-mix(in srgb, var(--ch-voice) 12%, var(--color-surface)); }
    .stat[data-tone='whatsapp'] .stat-ic { color: var(--ch-whatsapp); background: color-mix(in srgb, var(--ch-whatsapp) 14%, var(--color-surface)); }
    .stat[data-tone='email'] .stat-ic { color: var(--ch-email); background: color-mix(in srgb, var(--ch-email) 14%, var(--color-surface)); }
    .stat[data-tone='vcon'] .stat-ic { color: var(--ch-vcon); background: color-mix(in srgb, var(--ch-vcon) 14%, var(--color-surface)); }
    .stat[data-tone='team'] .stat-ic { color: var(--color-primary); background: var(--color-primary-soft); }
    .stat[data-tone='queue'] .stat-ic { color: var(--color-warning); background: var(--color-warning-soft); }
    .stat-body { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .stat-top { display: flex; align-items: center; gap: 8px; }
    .stat-val { font-size: 1.7rem; font-weight: 700; line-height: 1.1; letter-spacing: -.01em; }
    .stat-label { font-size: var(--text-sm); font-weight: 600; }
    .stat-cap { line-height: 1.3; }

    /* Body layout */
    .lm-body { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 18px; align-items: start; }
    .lm-main { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
    .lm-rail { display: flex; flex-direction: column; gap: 18px; position: sticky; top: 0; }

    .live-chip { background: var(--color-success-soft); color: var(--color-success); border-color: transparent; }

    /* Sessions */
    .sessions { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .sess { position: relative; display: flex; flex-direction: column; gap: 11px; text-align: left;
      padding: 14px 14px 14px 18px; border-radius: var(--r-md); border: 1px solid var(--color-border);
      background: var(--color-surface); transition: transform .12s ease, box-shadow .15s ease, border-color .15s ease; }
    .sess:hover { transform: translateY(-2px); box-shadow: var(--e2); border-color: var(--color-border-strong); cursor: pointer; }
    .sess-rail { position: absolute; left: 0; top: 0; bottom: 0; width: 4px; border-radius: var(--r-md) 0 0 var(--r-md); }
    .sess-rail[data-ch='voice'] { background: var(--ch-voice); }
    .sess-rail[data-ch='whatsapp'] { background: var(--ch-whatsapp); }
    .sess-rail[data-ch='email'] { background: var(--ch-email); }
    .sess-rail[data-ch='vcon'] { background: var(--ch-vcon); }
    .sess-person { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .sess-id { min-width: 0; }
    .sess-name { font-size: var(--text-sm); font-weight: 600; }
    .sess-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .sess-ch { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-cap); font-weight: 600; }
    .sess-ch[data-ch='voice'] { color: var(--ch-voice); }
    .sess-ch[data-ch='whatsapp'] { color: var(--ch-whatsapp); }
    .sess-ch[data-ch='email'] { color: var(--ch-email); }
    .sess-ch[data-ch='vcon'] { color: var(--ch-vcon); }
    .chip.dur { padding: 3px 8px; gap: 4px; }
    .chip.handler { padding: 3px 8px; }
    .chip.handler.ai { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); border-color: transparent; }
    .sess-signals { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; border-top: 1px dashed var(--color-border); padding-top: 10px; }
    .sig { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .sig .row { gap: 8px; }
    .conf-track { height: 6px; border-radius: 999px; background: var(--color-surface-alt); overflow: hidden; }
    .conf-fill { display: block; height: 100%; border-radius: 999px; transition: width .5s ease; }
    .conf-fill[data-band='high'] { background: var(--gradient-ai); }
    .conf-fill[data-band='med'] { background: var(--band-med); }
    .conf-fill[data-band='low'] { background: var(--band-low); }
    .sig .t-num[data-band='high'] { color: var(--color-accent); font-weight: 700; }
    .sig .t-num[data-band='med'] { color: var(--band-med); font-weight: 700; }
    .sig .t-num[data-band='low'] { color: var(--band-low); font-weight: 700; }
    .sess-intent { display: inline-flex; align-items: center; gap: 5px; }
    .sess-intent va-icon { color: var(--color-accent-2); flex: none; }

    /* Channel load */
    .loads { display: grid; grid-template-columns: 1fr 1fr; gap: 16px 22px; }
    .load { display: flex; flex-direction: column; gap: 6px; }
    .load-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .load-ch { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-sm); font-weight: 600; }
    .load-ch[data-ch='voice'] { color: var(--ch-voice); }
    .load-ch[data-ch='whatsapp'] { color: var(--ch-whatsapp); }
    .load-ch[data-ch='email'] { color: var(--ch-email); }
    .load-ch[data-ch='vcon'] { color: var(--ch-vcon); }
    .progress.warn > span { background: var(--color-warning); }
    .hot { color: var(--color-danger); font-weight: 600; }

    /* Activity stream */
    .stream { max-height: 360px; padding: 8px; display: flex; flex-direction: column; }
    .act { display: flex; align-items: flex-start; gap: 10px; padding: 9px 10px; border-radius: var(--r-md); }
    .act:hover { background: var(--color-surface-alt); }
    .act.fresh { animation: lm-flash 1.4s ease-out; }
    @keyframes lm-flash { 0% { background: rgba(var(--color-accent-rgb), .16); } 100% { background: transparent; } }
    .act-ic { width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center; flex: none; background: var(--color-surface-alt); color: var(--color-text-muted); }
    .act-ic[data-ch='voice'] { color: var(--ch-voice); } .act-ic[data-ch='whatsapp'] { color: var(--ch-whatsapp); }
    .act-ic[data-ch='email'] { color: var(--ch-email); } .act-ic[data-ch='vcon'] { color: var(--ch-vcon); }
    .act-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
    .act-text { font-size: var(--text-sm); }
    .ai-tag { font-weight: 700; color: var(--color-accent-2); }
    .ai-tag.human { color: var(--color-primary); }

    /* Alerts */
    .alerts { display: flex; flex-direction: column; gap: 8px; }
    .alert { display: flex; gap: 10px; text-align: left; padding: 11px 12px; border-radius: var(--r-md);
      border: 1px solid var(--color-border); background: var(--color-surface); transition: background .12s ease, border-color .12s ease; }
    .alert:hover { background: var(--color-surface-alt); cursor: pointer; }
    .alert-ic { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center; flex: none; }
    .alert[data-kind='confidence'] .alert-ic { background: rgba(var(--color-accent-2-rgb), .12); color: var(--color-accent-2); }
    .alert[data-kind='escalation'] .alert-ic { background: var(--color-warning-soft); color: var(--color-warning); }
    .alert[data-kind='failed'] .alert-ic { background: var(--color-danger-soft); color: var(--color-danger); }
    .alert.distress { border-color: color-mix(in srgb, var(--color-danger) 45%, var(--color-border)); background: var(--color-danger-soft); }
    .alert.distress .alert-ic { background: var(--color-danger); color: #fff; }
    .alert-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
    .alert-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .alert-title { font-size: var(--text-sm); font-weight: 600; }
    .alert-ts { margin-top: 1px; }
    .urg { font-size: 10px; font-weight: 700; text-transform: uppercase; padding: 3px 7px; border-radius: var(--r-pill); flex: none; }
    .urg[data-u='Critical'] { background: var(--color-danger); color: #fff; }
    .urg[data-u='High'] { background: var(--color-warning-soft); color: var(--color-warning); }
    .urg[data-u='Medium'] { background: var(--color-surface-alt); color: var(--color-text-muted); }
    .urg[data-u='Low'] { background: var(--color-surface-alt); color: var(--color-text-muted); }

    /* Team */
    .team { display: flex; flex-direction: column; gap: 10px; }
    .member { display: flex; align-items: center; gap: 10px; }
    .member-id { display: flex; flex-direction: column; gap: 0; min-width: 0; flex: 1; }
    .member-name { font-size: var(--text-sm); font-weight: 600; }
    .presence { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-cap); font-weight: 600; flex: none; }
    .presence[data-s='available'] { color: var(--color-success); }
    .presence[data-s='on-call'] { color: var(--color-warning); }
    .presence[data-s='away'] { color: var(--color-text-muted); }

    @media (max-width: 1200px) {
      .stats { grid-template-columns: repeat(3, 1fr); }
      .lm-body { grid-template-columns: 1fr; }
      .lm-rail { position: static; }
    }
    @media (max-width: 720px) {
      .stats { grid-template-columns: repeat(2, 1fr); }
      .sessions, .loads { grid-template-columns: 1fr; }
    }
  `],
})
export class LiveMonitorComponent implements OnDestroy {
  private store = inject(DataStore);
  private router = inject(Router);
  private toast = inject(ToastService);
  auth = inject(AuthService);
  counselor = inject(CounselorService);
  career = computed(() => this.counselor.active() === 'career');
  audience = computed(() => this.career() ? 'students' : 'candidates');
  aiLabel = computed(() => this.counselor.activeMeta().name + ' · AI');

  fmt = fmtInt;
  relTime = relTime;
  chIcon = (c: Channel) => CHANNEL_ICON[c];
  chLabel = (c: Channel) => CHANNEL_LABEL[c];

  /* ---- ticking live counters ---- */
  private tick = signal(0);
  reconnecting = signal(false);
  newestId = signal<string>('');

  private baseStats: FloorStat[] = [
    { key: 'voice', label: 'Live calls', icon: 'phone', channel: 'voice', tone: 'voice', value: 14, caption: 'Voice conversations', live: true, route: '/app/communications/voice' },
    { key: 'whatsapp', label: 'WhatsApp chats', icon: 'message-circle', channel: 'whatsapp', tone: 'whatsapp', value: 38, caption: 'Active threads', live: true, route: '/app/communications/whatsapp' },
    { key: 'vcon', label: 'Active V-Cons', icon: 'video', channel: 'vcon', tone: 'vcon', value: 3, caption: 'AI + human video', live: true, route: '/app/vcons' },
    { key: 'email', label: 'Emails sending', icon: 'mail', channel: 'email', tone: 'email', value: 126, caption: 'Queued this minute', live: true, route: '/app/communications/email' },
    { key: 'team', label: 'Counselors available', icon: 'users', tone: 'team', value: 6, caption: '9 on the floor', live: false, route: '/app/handoff' },
    { key: 'queue', label: 'Handoff queue', icon: 'inbox', tone: 'queue', value: 7, caption: 'Awaiting a human', live: true, route: '/app/handoff' },
  ];

  /** Counters jitter a little each tick to feel live, anchored to the base value. */
  stats = computed<FloorStat[]>(() => {
    const t = this.tick();
    return this.baseStats.map((s, i) => {
      if (!s.live) return s;
      const wobble = Math.round(Math.sin(t * 0.7 + i * 1.3) * (s.key === 'email' ? 9 : s.key === 'whatsapp' ? 4 : 2));
      return { ...s, value: Math.max(0, s.value + wobble) };
    });
  });

  totalActive = computed(() => {
    const s = this.stats();
    const sum = (k: string) => s.find(x => x.key === k)?.value ?? 0;
    return sum('voice') + sum('whatsapp') + sum('vcon');
  });

  lastUpdated = computed(() => { this.tick(); return 'just now'; });

  /* ---- live activity stream ---- */
  private streamItems = signal<ActivityItem[]>(this.store.activity().slice(0, 12));
  stream = computed(() => this.streamItems());

  private synthChannels: Channel[] = ['voice', 'whatsapp', 'email', 'vcon'];
  private admissionSynthLines: string[] = [
    'AI call connected — discussing B.Tech AI & Data Science',
    'WhatsApp reply received — asked about scholarship eligibility',
    'Scholarship guide email opened twice',
    'V-Con confirmed for tomorrow 4:00 PM',
    'Course brochure delivered & read',
    'High-intent candidate detected — probability +9%',
    'Aisha shared approved fee structure document',
    'Aisha escalated to a human counselor (low confidence)',
    'Application reminder sent on WhatsApp',
    'Parent joined the conversation — concerns recorded',
  ];
  private careerSynthLines: string[] = [
    'AI call connected — exploring Software Engineering pathway',
    'WhatsApp reply received — asked about salary bands for AI roles',
    'Skill-gap plan email opened twice',
    'Mentor-match V-Con confirmed for tomorrow 4:00 PM',
    'Pathway guide delivered & read',
    'High-readiness student detected — readiness +9%',
    'Vera shared approved skill framework document',
    'Vera escalated to a human counselor (low confidence)',
    'Upskilling reminder sent on WhatsApp',
    'Parent joined the conversation — career-stability concerns recorded',
  ];
  private get synthLines(): string[] { return this.career() ? this.careerSynthLines : this.admissionSynthLines; }
  private synthSent: Sentiment[] = ['pos', 'very-pos', 'neutral', 'neg', 'pos'];
  private synthSeq = 0;

  /* ---- live conversation sessions ---- */
  sessions = computed<LiveSession[]>(() => {
    this.tick();
    const cands = this.store.candidates();
    const picks = [cands[0], cands[3], cands[5], cands[8], cands[11], cands[14]].filter(Boolean);
    const chans: Channel[] = ['voice', 'whatsapp', 'vcon', 'whatsapp', 'voice', 'email'];
    const career = this.career();
    const intents = career
      ? [
          'Exploring career pathways', 'Reviewing skill-gap plan', 'Aptitude → pathway fit',
          'Salary-band question', 'Certifications recruiters value', 'Remote-work prospects',
        ]
      : [
          'Asking about placement records', 'Comparing fee structure', 'Parent reassurance · safety',
          'Scholarship eligibility check', 'Course curriculum walkthrough', 'EMI / payment options',
        ];
    const pathways = ['Software Engineering', 'Data Science & AI', 'Product / UX Design', 'Cybersecurity', 'Finance & Analytics', 'Healthcare & Bio'];
    const t = this.tick();
    return picks.map((c, i) => {
      const conf = Math.min(98, Math.max(38, c.conversionProbability - 2 + Math.round(Math.cos(t * 0.5 + i) * 5)));
      return {
        id: 'sess-' + c.candidateId,
        candidateId: c.candidateId,
        name: c.name,
        hue: c.avatarHue,
        course: career ? pathways[i % pathways.length] : c.preferredCourse,
        channel: chans[i % chans.length],
        actor: (i === 2 ? 'human' : 'ai') as 'ai' | 'human',
        durationSec: 95 + i * 47 + (t % 60),
        sentiment: c.sentiment,
        aiConfidence: conf,
        intent: intents[i % intents.length],
        route: this.routeFor(chans[i % chans.length], c.candidateId),
      };
    });
  });

  /* ---- today funnel (live, smaller numbers than the global cycle funnel) ---- */
  todayFunnel = computed<FunnelStage[]>(() => {
    this.tick();
    if (this.career()) {
      return [
        { key: 't-engaged', label: 'Engaged today', count: 286, dropOffPct: 0, trendPct: 16 },
        { key: 't-interests', label: 'Interests profiled', count: 214, dropOffPct: 25.2, trendPct: 12 },
        { key: 't-aptitude', label: 'Aptitude assessed', count: 138, dropOffPct: 35.5, trendPct: 9 },
        { key: 't-pathway', label: 'Pathway recommended', count: 96, dropOffPct: 30.4, trendPct: 18 },
        { key: 't-skillplan', label: 'Skill plan created', count: 52, dropOffPct: 45.8, trendPct: 14 },
        { key: 't-ready', label: 'Career-ready step', count: 21, dropOffPct: 59.6, trendPct: 20 },
      ];
    }
    return [
      { key: 't-captured', label: 'New leads today', count: 312, dropOffPct: 0, trendPct: 14 },
      { key: 't-contacted', label: 'Contacted', count: 248, dropOffPct: 20.5, trendPct: 9 },
      { key: 't-engaged', label: 'In conversation', count: 163, dropOffPct: 34.3, trendPct: 12 },
      { key: 't-interested', label: 'Interested', count: 94, dropOffPct: 42.3, trendPct: 7 },
      { key: 't-registered', label: 'Registered', count: 41, dropOffPct: 56.4, trendPct: 11 },
      { key: 't-fee', label: 'Fee paid', count: 18, dropOffPct: 56.1, trendPct: 16 },
    ];
  });

  /* ---- channel load ---- */
  channelLoads = computed<ChannelLoad[]>(() => {
    const s = this.stats();
    const v = (k: string) => s.find(x => x.key === k)?.value ?? 0;
    return [
      { channel: 'voice', label: 'Voice lines', active: v('voice'), capacity: 24 },
      { channel: 'whatsapp', label: 'WhatsApp', active: v('whatsapp'), capacity: 60 },
      { channel: 'email', label: 'Email sender', active: v('email'), capacity: 200 },
      { channel: 'vcon', label: 'V-Con rooms', active: v('vcon'), capacity: 8 },
    ];
  });

  /* ---- alerts (from escalations + synthetic confidence / failed) ---- */
  alerts = computed<FloorAlert[]>(() => {
    const escs = this.store.escalations().filter(e => e.status === 'Open' || e.status === 'Claimed').slice(0, 4);
    const fromEsc: FloorAlert[] = escs.map(e => ({
      id: e.escalationId,
      kind: 'escalation',
      candidateId: e.candidateId,
      title: e.candidateName + ' · ' + e.reason,
      detail: e.aiSummary,
      urgency: e.urgency,
      ts: e.createdAt,
      distress: e.distress,
    }));
    const name = this.counselor.activeMeta().name;
    const synthetic: FloorAlert[] = this.career()
      ? [
          { id: 'cf-1', kind: 'confidence', title: 'Low AI confidence on salary-band question', detail: `${name} declined to quote an unapproved salary range and paused — awaiting human review.`, urgency: 'High', ts: this.store.activity()[1]?.ts ?? new Date().toISOString() },
          { id: 'fa-1', kind: 'failed', title: 'Voice call failed · 3 attempts', detail: 'No answer from the student after retries. Recommend a WhatsApp follow-up within consent window.', urgency: 'Medium', ts: this.store.activity()[2]?.ts ?? new Date().toISOString() },
        ]
      : [
          { id: 'cf-1', kind: 'confidence', title: 'Low AI confidence on fee waiver question', detail: `${name} declined to commit to an unapproved concession and paused — awaiting human review.`, urgency: 'High', ts: this.store.activity()[1]?.ts ?? new Date().toISOString() },
          { id: 'fa-1', kind: 'failed', title: 'Voice call failed · 3 attempts', detail: 'No answer from candidate after retries. Recommend a WhatsApp follow-up within consent window.', urgency: 'Medium', ts: this.store.activity()[2]?.ts ?? new Date().toISOString() },
        ];
    return [...fromEsc, ...synthetic].sort((a, b) => this.urgRank(b.urgency) - this.urgRank(a.urgency));
  });

  /* ---- counselor availability ---- */
  counselors = [
    { name: 'Meera Nair', role: 'Human Counselor', hue: 280, status: 'available' as const },
    { name: 'Rahul Desai', role: 'Admission Manager', hue: 200, status: 'on-call' as const },
    { name: 'Imran Sheikh', role: 'AI Supervisor', hue: 150, status: 'available' as const },
    { name: 'Sneha Kapoor', role: 'Human Counselor', hue: 330, status: 'on-call' as const },
    { name: 'Priya Menon', role: 'Admission Director', hue: 222, status: 'away' as const },
  ];

  /* ---- intervals ---- */
  private counterTimer = setInterval(() => this.tick.update(v => v + 1), 3000);
  private streamTimer = setInterval(() => this.pushSynthetic(), 4000);

  ngOnDestroy() {
    clearInterval(this.counterTimer);
    clearInterval(this.streamTimer);
  }

  private pushSynthetic() {
    if (this.reconnecting()) return;
    const cands = this.store.candidates();
    const c = cands[Math.floor(Math.random() * Math.min(20, cands.length))];
    const ix = this.synthSeq % this.synthLines.length;
    const ch = this.synthChannels[this.synthSeq % this.synthChannels.length];
    const item: ActivityItem = {
      id: 'live-' + (this.synthSeq++),
      channel: ch,
      actor: this.synthLines[ix].includes('Parent') || this.synthLines[ix].includes('escalated') ? 'human' : 'ai',
      candidate: c?.name ?? 'Candidate',
      text: this.synthLines[ix],
      ts: new Date().toISOString(),
      sentiment: this.synthSent[this.synthSeq % this.synthSent.length],
    };
    this.newestId.set(item.id);
    this.streamItems.update(list => [item, ...list].slice(0, 16));
  }

  /* ---- helpers ---- */
  private routeFor(ch: Channel, id: string): string {
    if (ch === 'whatsapp') return '/app/communications/whatsapp/' + id;
    if (ch === 'voice') return '/app/communications/voice';
    if (ch === 'vcon') return '/app/vcons';
    if (ch === 'email') return '/app/communications/email';
    return '/app/communications';
  }
  private urgRank(u: FloorAlert['urgency']) { return u === 'Critical' ? 3 : u === 'High' ? 2 : u === 'Medium' ? 1 : 0; }

  dur(sec: number): string {
    const m = Math.floor(sec / 60); const s = sec % 60;
    return m + ':' + s.toString().padStart(2, '0');
  }
  confBand(v: number): 'low' | 'med' | 'high' { return v < 60 ? 'low' : v <= 80 ? 'med' : 'high'; }
  loadPct(l: ChannelLoad): number { return Math.min(100, Math.round((l.active / l.capacity) * 100)); }
  alertIcon(kind: FloorAlert['kind']): string {
    return kind === 'confidence' ? 'gauge' : kind === 'failed' ? 'alert-circle' : 'alert-triangle';
  }
  presenceLabel(s: string): string { return s === 'available' ? 'Available' : s === 'on-call' ? 'On call' : 'Away'; }

  go(url: string) { this.router.navigateByUrl(url); }
  openStat(s: FloorStat) { if (s.route) this.router.navigateByUrl(s.route); }
  openAlert(al: FloorAlert) {
    this.toast.info('Opening human handoff queue — ' + al.title);
    this.router.navigateByUrl('/app/handoff');
  }
  drillStage(s: FunnelStage) {
    this.toast.info('Opening ' + s.label + ' (' + s.count.toLocaleString('en-IN') + ' today)');
    this.router.navigateByUrl('/app/crm');
  }
  manualRefresh() { this.tick.update(v => v + 1); this.toast.success('Live stream refreshed.'); }
  toggleReconnect() {
    this.reconnecting.update(v => !v);
    if (this.reconnecting()) this.toast.warning('Simulating a gateway drop — reconnecting banner shown.');
    else this.toast.success('Reconnected to the live gateway.');
  }
}
