import { Injectable, computed, signal } from '@angular/core';

/** The two AI Virtual Humanoid counselors an institution can run — one, the other, or both. */
export type CounselorType = 'admission' | 'career';

export interface CounselorMeta {
  type: CounselorType;
  name: string;        // the humanoid's name
  title: string;       // "AI Admission Counselor"
  short: string;       // "Admission"
  tagline: string;
  icon: string;        // sidebar/chip icon
  gradient: string;    // css gradient token
  accent: string;      // css color token
}

export const COUNSELORS: Record<CounselorType, CounselorMeta> = {
  admission: {
    type: 'admission',
    name: 'Aisha',
    title: 'AI Admission Counselor',
    short: 'Admission',
    tagline: 'Proactively counsels candidates and parents through admissions — from approved knowledge only.',
    icon: 'graduation-cap',
    gradient: 'var(--gradient-ai)',
    accent: 'var(--color-accent)',
  },
  career: {
    type: 'career',
    name: 'Vera',
    title: 'AI Career Counselor',
    short: 'Career',
    tagline: 'Discovers strengths, maps career pathways, and guides upskilling — from approved guidance only.',
    icon: 'compass',
    gradient: 'var(--gradient-career)',
    accent: 'var(--color-career)',
  },
};

@Injectable({ providedIn: 'root' })
export class CounselorService {
  /** Which counselors the institution has enabled (tenant config). Demo default: both. */
  readonly enabled = signal<CounselorType[]>(['admission', 'career']);
  /** The counselor currently in focus when both are enabled. */
  readonly active = signal<CounselorType>('admission');

  readonly both = computed(() => this.enabled().length === 2);
  readonly only = computed<CounselorType | null>(() => (this.enabled().length === 1 ? this.enabled()[0] : null));
  readonly activeMeta = computed(() => COUNSELORS[this.active()]);
  readonly enabledMetas = computed(() => this.enabled().map(t => COUNSELORS[t]));

  meta(type: CounselorType) { return COUNSELORS[type]; }
  isEnabled(type: CounselorType) { return this.enabled().includes(type); }

  setActive(type: CounselorType) { if (this.isEnabled(type)) this.active.set(type); }

  toggleEnabled(type: CounselorType) {
    this.enabled.update(list => {
      const has = list.includes(type);
      // never allow zero enabled
      if (has && list.length === 1) return list;
      const next = has ? list.filter(t => t !== type) : [...list, type];
      return (['admission', 'career'] as CounselorType[]).filter(t => next.includes(t));
    });
    if (!this.isEnabled(this.active())) this.active.set(this.enabled()[0]);
  }
}
