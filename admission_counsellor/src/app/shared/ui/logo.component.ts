import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

/** Admission Counsellor wordmark + symbol (§3.2). "Counsellor" carries the cyan→violet gradient. */
@Component({
  selector: 'va-logo',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="logo" [class.mark-only]="markOnly">
      <svg class="mark" [attr.width]="markSize" [attr.height]="markSize" viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <defs>
          <linearGradient [attr.id]="gid" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
            <stop stop-color="#22D3EE"/><stop offset="1" stop-color="#7C3AED"/>
          </linearGradient>
        </defs>
        <rect width="64" height="64" rx="15" [attr.fill]="markBg"/>
        <path d="M16 18 L32 44 L48 18" [attr.stroke]="'url(#' + gid + ')'" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="32" cy="44" r="5.5" [attr.fill]="'url(#' + gid + ')'"/>
        <circle cx="32" cy="44" r="10.5" [attr.stroke]="'url(#' + gid + ')'" stroke-width="1.6" opacity="0.45"/>
      </svg>
      @if (!markOnly) {
        <span class="word" [style.font-size.px]="size">Admission&nbsp;<span class="grad">Counsellor</span></span>
      }
    </span>`,
  styles: [`
    .logo { display: inline-flex; align-items: center; gap: 10px; }
    .mark { flex: none; border-radius: 14px; }
    .word { font-family: var(--font-display); font-weight: 800; letter-spacing: -.02em; color: var(--color-text); line-height: 1; }
    .grad { background: var(--gradient-ai); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
  `],
})
export class LogoComponent {
  @Input() size = 22;
  @Input() markSize = 32;
  @Input() markOnly = false;
  @Input() onDark = false;
  gid = 'lg-' + Math.floor(Math.random() * 1e6);
  get markBg() { return this.onDark ? '#FFFFFF14' : '#1E3A8A'; }
}
