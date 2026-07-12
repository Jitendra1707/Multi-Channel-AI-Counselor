import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/ui/icon.component';
import { LogoComponent } from '../../shared/ui/logo.component';
import { AiAvatarComponent } from '../../shared/ui/avatar.component';
import { RevealDirective } from '../../shared/ui/reveal.directive';

interface Feature { icon: string; title: string; body: string; tag?: string; }
interface JourneyStep { n: number; icon: string; title: string; body: string; }
interface Journey { variant: 'admission' | 'career'; counselor: string; kicker: string; title: string; sub: string; steps: JourneyStep[]; }

@Component({
  selector: 'va-landing',
  standalone: true,
  imports: [RouterLink, IconComponent, LogoComponent, AiAvatarComponent, RevealDirective],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './landing.component.html',
  styleUrl: './landing.component.scss',
})
export class LandingComponent {
  channels = [
    { icon: 'phone', label: 'Voice', color: 'var(--ch-voice)' },
    { icon: 'message-circle', label: 'WhatsApp', color: 'var(--ch-whatsapp)' },
    { icon: 'mail', label: 'Email', color: 'var(--ch-email)' },
    { icon: 'video', label: 'V-Cons', color: 'var(--ch-vcon)' },
    { icon: 'users', label: 'CRM', color: 'var(--color-primary)' },
    { icon: 'bar-chart', label: 'Analytics', color: 'var(--color-accent-2)' },
  ];

  proofMetrics = [
    { value: '3×', label: 'faster first contact' },
    { value: '100%', label: 'follow-up coverage' },
    { value: '+24%', label: 'application conversion' },
    { value: '24/7', label: 'in the candidate’s language' },
  ];

  humanoids = [
    {
      variant: 'admission' as const, name: 'Aisha', title: 'AI Admission Counselor',
      blurb: 'Aisha guides candidates and parents through every admission decision. She recommends courses, explains fees, scholarships and eligibility, helps complete applications, and turns enquiries into enrolments.',
      points: ['Omnichannel outreach and follow-up', 'Fee, scholarship and eligibility guidance', 'Applications and registration tracking', 'Conversion analytics and reports'],
    },
    {
      variant: 'career' as const, name: 'Vera', title: 'AI Career Counselor',
      blurb: 'Vera helps students discover their strengths and turn them into a plan. She maps career pathways, pinpoints skill gaps, and guides upskilling and placements long after admission is done.',
      points: ['Aptitude and interest profiling', 'Career-pathway and course mapping', 'Skill-gap and upskilling guidance', 'Career-readiness and outcome reports'],
    },
  ];

  features: Feature[] = [
    { icon: 'bot', title: 'AI Virtual Humanoid Counselor', body: 'A lifelike counselor that speaks only from the knowledge your institution has approved, and always discloses that it is an AI.', tag: 'Responsible AI' },
    { icon: 'message-square', title: 'Omnichannel communication', body: 'Voice, WhatsApp, email and video consultations come together as one auditable conversation per student.' },
    { icon: 'users', title: 'CRM & lead management', body: 'Upload, validate, de-duplicate and segment leads. Every record is scored, tracked and given an owner.' },
    { icon: 'book-open', title: 'Knowledge Management', body: 'Your institutional source of truth. The counselor learns only from approval-governed documents, and nothing else.', tag: 'Approval-gated' },
    { icon: 'brain', title: 'Controlled self-learning', body: 'Knowledge gaps become approval-gated learning items, so the AI never learns unsupervised.' },
    { icon: 'shield-check', title: 'Guardrails & compliance', body: 'Define exactly what can be said. The AI cannot exceed approved knowledge, invent fees or promise admission.' },
    { icon: 'video', title: 'V-Cons video consultations', body: 'Your AI avatar and your human counselors meet candidates and parents face to face.' },
    { icon: 'bar-chart', title: 'Conversion analytics', body: 'Live funnel, demand and sentiment, plus AI versus human performance, with AI-narrated reporting.' },
  ];

  journeys: Journey[] = [
    {
      variant: 'admission', counselor: 'Aisha', kicker: 'Aisha · Admission',
      title: 'The admission journey', sub: 'From a raw list of leads to confirmed enrolments.',
      steps: [
        { n: 1, icon: 'upload', title: 'Capture leads', body: 'Import your candidate list. Admission Counsellor validates, de-duplicates and records consent at the source.' },
        { n: 2, icon: 'bot', title: 'Aisha engages', body: 'Aisha calls and messages every candidate from approved knowledge across voice, WhatsApp and email.' },
        { n: 3, icon: 'headphones', title: 'Humans step in', body: 'Sensitive or low-confidence moments escalate to your team with the full conversation history.' },
        { n: 4, icon: 'graduation-cap', title: 'Convert to admission', body: 'Track each candidate from first contact to confirmed admission, and see what drove the result.' },
      ],
    },
    {
      variant: 'career', counselor: 'Vera', kicker: 'Vera · Career',
      title: 'The career journey', sub: 'From a student’s strengths to a confident placement.',
      steps: [
        { n: 1, icon: 'compass', title: 'Profile strengths', body: 'Students complete aptitude and interest profiling. Vera builds a clear picture of their strengths.' },
        { n: 2, icon: 'git-branch', title: 'Map pathways', body: 'Vera matches strengths to career pathways, and to the programs and courses that lead there.' },
        { n: 3, icon: 'trending-up', title: 'Close skill gaps', body: 'Pinpoint missing skills and guide targeted upskilling using approved, institution-backed resources.' },
        { n: 4, icon: 'briefcase', title: 'Guide to placement', body: 'Connect students with mentors and track career-readiness all the way to placement.' },
      ],
    },
  ];

  security = [
    'Multi-tenant data isolation & RBAC', 'MFA + SSO (SAML / OIDC)', 'Encryption at rest & in transit',
    'Comprehensive audit logging', 'Consent & opt-out management', 'Regional data residency',
    'AI identity disclosure', 'Vault-managed secrets',
  ];

  integrations = ['Telephony', 'WhatsApp Business', 'SendGrid / SES', 'Google / Microsoft Calendar', 'WebRTC', 'Payment gateways', 'University ERP / SIS', 'SAML / OIDC'];

  useCases = [
    { icon: 'graduation-cap', title: 'Universities', body: 'Scale multi-campus admissions counseling with institution-controlled messaging.' },
    { icon: 'building', title: 'Colleges', body: 'Convert more applicants with patient, accurate guidance at every stage.' },
    { icon: 'zap', title: 'EdTech', body: 'Embed responsible AI counseling into your enrolment funnel.' },
    { icon: 'star', title: 'Training & Certification', body: 'Engage learners and parents with trustworthy, on-brand answers.' },
  ];

  year = 2026;
}
