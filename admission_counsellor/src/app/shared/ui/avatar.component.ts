import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

/** Initials avatar with a deterministic hue. */
@Component({
  selector: 'va-avatar',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="av" [style.width.px]="size" [style.height.px]="size"
              [style.background]="bg()" [style.font-size.px]="size * 0.4">{{ initials() }}</span>`,
  styles: [`
    .av { display: inline-flex; align-items: center; justify-content: center; border-radius: 50%;
      color: #fff; font-weight: 700; flex: none; letter-spacing: .02em; box-shadow: inset 0 0 0 1px rgba(255,255,255,.15); }
  `],
})
export class AvatarComponent {
  @Input({ required: true }) name = '';
  @Input() hue = 222;
  @Input() size = 36;
  initials() { return this.name.split(' ').filter(Boolean).map(p => p[0]).join('').slice(0, 2).toUpperCase(); }
  bg() { return `linear-gradient(135deg, hsl(${this.hue} 70% 52%), hsl(${(this.hue + 40) % 360} 65% 42%))`; }
}

/** The AI Virtual Humanoid Counselor presence — always carries a non-removable "AI" badge (§3.2 / §22). */
@Component({
  selector: 'va-ai-avatar',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="aiav" [style.width.px]="size" [style.height.px]="size" [class.glow]="glow"
          [attr.data-variant]="variant" [style.background]="variant === 'career' ? 'var(--gradient-career)' : 'var(--gradient-ai)'">
      <svg viewBox="0 0 24 24" fill="none" [style.width.%]="62" [style.height.%]="62" aria-hidden="true">
        <path d="M12 8V4H8" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
        <rect x="4" y="8" width="16" height="12" rx="3" stroke="#fff" stroke-width="1.6"/>
        <circle cx="9" cy="14" r="1.3" fill="#fff"/><circle cx="15" cy="14" r="1.3" fill="#fff"/>
        <path d="M9.5 17.5h5" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>
      </svg>
      <span class="badge">AI</span>
    </span>`,
  styles: [`
    .aiav { position: relative; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%;
      background: var(--gradient-ai); flex: none; box-shadow: var(--e1); }
    .aiav.glow { box-shadow: 0 0 0 4px rgba(34,211,238,.18), 0 8px 24px rgba(124,58,237,.25); }
    .aiav.glow[data-variant='career'] { box-shadow: 0 0 0 4px rgba(45,212,191,.20), 0 8px 24px rgba(14,165,166,.28); }
    .badge { position: absolute; right: -4px; bottom: -4px; background: var(--color-text); color: var(--color-bg);
      font-size: 9px; font-weight: 800; letter-spacing: .04em; padding: 2px 5px; border-radius: 999px;
      border: 2px solid var(--color-surface); line-height: 1; }
  `],
})
export class AiAvatarComponent {
  @Input() size = 40;
  @Input() glow = false;
  @Input() variant: 'admission' | 'career' = 'admission';
}
