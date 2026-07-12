/* =====================================================================
   Admission Counsellor — Deterministic mock-data seed.
   A small seeded PRNG keeps the demo data stable across reloads.
   ===================================================================== */
import {
  Candidate, CommEvent, KmsDoc, ApprovalRequest, Escalation, Application,
  ReferenceProvider, Metric, FunnelStage, BarDatum, InsightCard, ActivityItem,
  AppNotification, CandidateStage, Sentiment, Band, Channel, JourneyEvent, ChatMessage,
} from '../domain/models';

/* ---- seeded PRNG ---- */
let _seed = 1337;
function rnd(): number { _seed = (_seed * 9301 + 49297) % 233280; return _seed / 233280; }
function pick<T>(a: T[]): T { return a[Math.floor(rnd() * a.length)]; }
function int(min: number, max: number): number { return Math.floor(rnd() * (max - min + 1)) + min; }
function resetSeed() { _seed = 1337; }

const FIRST = ['Aarav','Diya','Vihaan','Ananya','Arjun','Saanvi','Reyansh','Aadhya','Krishna','Ishaan',
  'Myra','Kabir','Anika','Vivaan','Priya','Rohan','Meera','Aryan','Kavya','Dev','Tara','Ved','Nisha',
  'Aditya','Riya','Karan','Sneha','Yash','Pooja','Manav','Zara','Imran','Fatima','Joseph','Grace'];
const LAST = ['Sharma','Patel','Reddy','Nair','Iyer','Gupta','Khan','Mehta','Rao','Das','Bose','Menon',
  'Kapoor','Joshi','Verma','Chopra','Pillai','Banerjee','Kulkarni','Sinha','Fernandes','Thomas'];
const CITIES: [string,string][] = [['Hyderabad','Telangana'],['Bengaluru','Karnataka'],['Mumbai','Maharashtra'],
  ['Chennai','Tamil Nadu'],['Pune','Maharashtra'],['Delhi','Delhi'],['Kochi','Kerala'],['Jaipur','Rajasthan'],
  ['Ahmedabad','Gujarat'],['Kolkata','West Bengal'],['Coimbatore','Tamil Nadu'],['Vizag','Andhra Pradesh']];
const COURSES = ['B.Tech Computer Science','B.Tech AI & Data Science','MBA','B.Com (Hons)','B.Sc Data Science',
  'B.Des UX','BBA','M.Tech AI','B.Tech Mechanical','B.A Economics','MCA','B.Pharm'];
const INTERESTS = ['Software Engineering','Data Science','Product Design','Entrepreneurship','Finance',
  'Robotics','Marketing','Cybersecurity','Research','Healthcare','Cloud','Game Dev'];
const SOURCES = ['Website','Education Fair','Social Media','Tele-calling List','Webinar','Walk-in Import',
  'Paid Advertisement','School Partner','Alumni Referral'];
const PROVIDERS = ['BrightFuture Consultants','Apex Edu Partners','Sunrise School','Alumni Network','CampusConnect Agents','—'];
// Admission CRM leads are always handled by Aisha, the AI Admission Counsellor.
// (Vera, the AI Career Counsellor, owns the separate career-side data.)
const AI_NAMES = ['Aisha'];
const HUMANS = ['Priya Menon','Rahul Desai','Sneha Kapoor','Imran Sheikh'];
const BACKGROUNDS = ['12th CBSE · PCM · 88%','12th State Board · 91%','Diploma · CSE · 79%',
  'B.Sc graduate · 7.8 CGPA','12th ICSE · Commerce · 84%','12th CBSE · PCB · 90%'];

const STAGES: CandidateStage[] = ['New Lead','Imported','Validated','Contact Pending','Contacted',
  'Interested','Needs More Information','Parent Discussion Required','V-Con Scheduled','Counseling Completed',
  'Registration Link Sent','Registered','Application Started','Application Fee Pending','Application Fee Paid',
  'Application Submitted','Admission Offered','Admitted'];
const SENTI: Sentiment[] = ['very-neg','neg','neutral','pos','very-pos'];
const BANDS: Band[] = ['low','med','high'];

function band(p: number): Band { return p < 40 ? 'low' : p <= 70 ? 'med' : 'high'; }
function isoDaysAgo(d: number, h = 0): string { const t = new Date('2026-06-14T09:30:00'); t.setDate(t.getDate() - d); t.setHours(t.getHours() - h); return t.toISOString(); }
function isoDaysAhead(d: number): string { const t = new Date('2026-06-14T09:30:00'); t.setDate(t.getDate() + d); return t.toISOString(); }

export function buildCandidates(n = 42): Candidate[] {
  resetSeed();
  const list: Candidate[] = [];
  for (let i = 0; i < n; i++) {
    const fn = pick(FIRST), ln = pick(LAST);
    const [city, region] = pick(CITIES);
    const prob = int(8, 96);
    const stageIdx = Math.min(STAGES.length - 1, Math.floor((prob / 100) * STAGES.length) + int(-2, 1));
    const stage = STAGES[Math.max(0, stageIdx)];
    const course = pick(COURSES);
    const senti = SENTI[Math.min(4, Math.floor(prob / 22))];
    const ai = pick(AI_NAMES);
    const hasParent = rnd() > 0.45;
    const id = `cand-${(i + 1).toString().padStart(3, '0')}`;
    const interests = [pick(INTERESTS), pick(INTERESTS)].filter((v, ix, a) => a.indexOf(v) === ix);
    const nextActions = [
      { label: 'Send WhatsApp fee reminder', channel: 'whatsapp' as Channel, reason: 'High intent, fee not yet discussed' },
      { label: 'Schedule a V-Con with parents', channel: 'vcon' as Channel, reason: 'Parent reassurance pending' },
      { label: 'Call to confirm course interest', channel: 'voice' as Channel, reason: 'No successful contact yet' },
      { label: 'Email scholarship guide', channel: 'email' as Channel, reason: 'Scholarship interest detected' },
      { label: 'Send registration link', channel: 'whatsapp' as Channel, reason: 'Counseling complete, ready to register' },
    ];
    list.push({
      candidateId: id,
      name: `${fn} ${ln}`,
      avatarHue: int(0, 360),
      mobile: `+91 9${int(100000000, 999999999)}`,
      whatsapp: `+91 9${int(100000000, 999999999)}`,
      email: `${fn.toLowerCase()}.${ln.toLowerCase()}@gmail.com`,
      city, region, country: 'India',
      academicBackground: pick(BACKGROUNDS),
      careerInterests: interests,
      preferredCourse: course,
      budgetRange: pick(['₹2–4L / yr','₹4–7L / yr','₹7–12L / yr','₹1–2L / yr']),
      budgetSensitivity: pick(BANDS),
      scholarshipInterest: rnd() > 0.4,
      parents: hasParent ? [{
        parentId: `par-${id}`, candidateId: id,
        name: `${pick(['Suresh','Lata','Ramesh','Anita','Vijay','Geeta'])} ${ln}`,
        relationship: pick(['Father','Mother','Guardian']),
        mobile: `+91 9${int(100000000, 999999999)}`,
        whatsapp: `+91 9${int(100000000, 999999999)}`,
        email: `${ln.toLowerCase()}.family@gmail.com`,
        preferredLanguage: pick(['English','Hindi','Telugu','Tamil','Marathi']),
        concerns: pick([['Placement assurance','Hostel safety'],['Fees & scholarships'],['Course reputation','Job prospects'],['Distance from home']]),
        sentiment: pick(SENTI), lastContacted: rnd() > 0.5 ? isoDaysAgo(int(1, 9)) : undefined,
        consentToDiscuss: rnd() > 0.2,
      }] : [],
      consent: { call: rnd() > 0.1, whatsapp: rnd() > 0.15, email: rnd() > 0.1, recording: rnd() > 0.4, capturedAt: isoDaysAgo(int(2, 30)), source: 'Import' },
      leadSource: pick(SOURCES),
      referenceProvider: pick(PROVIDERS),
      currentStage: stage,
      conversionProbability: prob,
      dropOffRisk: prob > 60 && (stage.includes('Pending') || stage === 'Application Started') ? 'high' : band(100 - prob),
      sentiment: senti,
      assignedAiCounselor: ai,
      assignedHumanCounselor: rnd() > 0.7 ? pick(HUMANS) : undefined,
      parentEngagement: hasParent ? pick(['Pending','Engaged','Concerns Raised']) : 'None',
      applicationStatus: stage.includes('Application') || stage.includes('Admi') ? stage : '—',
      tags: rnd() > 0.7 ? [pick(['VIP reference','High intent','Repeat enquiry','International'])] : [],
      duplicate: rnd() > 0.92,
      doNotContact: rnd() > 0.95,
      lastContacted: isoDaysAgo(int(0, 14), int(0, 20)),
      nextFollowUp: rnd() > 0.3 ? isoDaysAhead(int(0, 6)) : undefined,
      createdBy: pick(['Excel Import','Web Form','Manual','CRM Sync']),
      createdAt: isoDaysAgo(int(5, 60)),
      pendingQuestions: rnd() > 0.5 ? [pick(['Internship partners for AI program?','Hostel fee for girls?','EMI options for tuition?','Scholarship cutoff for Data Science?'])] : [],
      lastAiSummary: pick([
        'Candidate is interested in AI & Data Science; asked about placements and scholarship eligibility. Wants parents involved before deciding.',
        'Strong intent for MBA; comparing fee structure with two other institutions. Sensitive to total cost.',
        'Exploring design programs; unsure between UX and product. Requested portfolio guidance and a V-Con.',
        'Asked detailed fee questions; flagged for scholarship eligibility check. Parent to join next call.',
      ]),
      recommendedNextAction: pick(nextActions),
    });
  }
  // A couple of guaranteed "showcase" rich profiles up top
  list[0] = { ...list[0], name: 'Ananya Reddy', city: 'Hyderabad', region: 'Telangana', preferredCourse: 'B.Tech AI & Data Science',
    conversionProbability: 82, dropOffRisk: 'med', currentStage: 'Parent Discussion Required', sentiment: 'pos',
    scholarshipInterest: true, parentEngagement: 'Concerns Raised', tags: ['High intent'],
    careerInterests: ['Data Science','Research'],
    lastAiSummary: 'Ananya is highly interested in the B.Tech AI & Data Science program and asked detailed questions about internship partners and placement records. Her father has concerns about hostel safety and wants placement assurance. Scholarship eligibility check pending. Recommended a V-Con with parents.',
    recommendedNextAction: { label: 'Schedule a V-Con with parents', channel: 'vcon', reason: 'Parent reassurance pending; high conversion probability' },
    pendingQuestions: ['Internship partners for the B.Tech AI program?','Merit scholarship cutoff for Data Science?'] };
  return list;
}

export function buildJourney(c: Candidate): JourneyEvent[] {
  resetSeed();
  const ev: JourneyEvent[] = [];
  const push = (d: number, type: string, label: string, channel: Channel | 'system', owner: any, summary: string, s?: Sentiment, pd?: number, docs?: string[]) =>
    ev.push({ id: `je-${c.candidateId}-${ev.length}`, type, label, channel, owner, ts: isoDaysAgo(d), summary, sentiment: s, probabilityDelta: pd, docsShared: docs });
  push(40, 'lead', 'Lead created', 'system', 'system', `Imported from ${c.leadSource}.`);
  push(38, 'call', 'First call attempted', 'voice', 'ai', `${c.assignedAiCounselor} introduced itself as an AI counselor for Northgate University.`, 'neutral', 4);
  push(36, 'call', 'First successful contact', 'voice', 'ai', `Discussed interest in ${c.preferredCourse}. Candidate engaged and positive.`, 'pos', 12);
  push(33, 'whatsapp', 'Course brochure shared', 'whatsapp', 'ai', `Shared ${c.preferredCourse} brochure and curriculum.`, 'pos', 6, ['Course brochure','Curriculum']);
  if (c.scholarshipInterest) push(28, 'email', 'Scholarship guide emailed', 'email', 'ai', 'Sent merit & need-based scholarship guide. Opened twice.', 'pos', 8, ['Scholarship policy']);
  push(20, 'note', 'Fee explained', 'note', 'ai', 'Explained fee structure from approved document; candidate asked about EMI options.', 'neutral', 3);
  if (c.parents.length) push(14, 'call', 'Parent contacted', 'voice', 'ai', `Spoke with ${c.parents[0].name} about placements and safety. Concerns recorded.`, c.parents[0].sentiment, 5);
  if (c.currentStage === 'Parent Discussion Required' || c.currentStage === 'V-Con Scheduled')
    push(2, 'vcon', 'V-Con scheduled', 'vcon', 'human', 'Video consultation booked with candidate and parent for course + placement walkthrough.', 'pos', 9);
  return ev;
}

export function buildChat(c: Candidate): ChatMessage[] {
  return [
    { id: 'm1', author: 'ai', kind: 'text', ts: isoDaysAgo(3, 5), status: 'read', text: `Hi ${c.name.split(' ')[0]}, I'm ${c.assignedAiCounselor}, an AI admission counselor for Northgate University. Is now a good time to talk about your study options?` },
    { id: 'm2', author: 'candidate', kind: 'text', ts: isoDaysAgo(3, 4), text: `Yes. I'm interested in ${c.preferredCourse}. What are the placement records?` },
    { id: 'm3', author: 'ai', kind: 'text', ts: isoDaysAgo(3, 4), status: 'read', text: `Great choice! I can share our official placement report. For specific company-wise figures, I'll send the approved document so you have accurate numbers.` },
    { id: 'm4', author: 'ai', kind: 'course-card', ts: isoDaysAgo(3, 4), status: 'read', text: '', cardTitle: c.preferredCourse, cardMeta: '4 years · Approved curriculum · Placement report attached' },
    { id: 'm5', author: 'candidate', kind: 'text', ts: isoDaysAgo(3, 3), text: 'And what about scholarships? My parents are concerned about the fees.' },
    { id: 'm6', author: 'ai', kind: 'scholarship-card', ts: isoDaysAgo(3, 3), status: 'read', text: '', cardTitle: 'Merit Scholarship 2026', cardMeta: 'Up to 40% tuition · Eligibility: 85%+ in 12th' },
    { id: 'm7', author: 'ai', kind: 'text', ts: isoDaysAgo(3, 3), status: 'delivered', text: `Would it help if I set up a short video call so your parents can ask questions directly? I can also invite a human counselor.` },
  ];
}

export function buildKms(): KmsDoc[] {
  resetSeed();
  const cats = ['Course Brochure','Fee Structure','Scholarship Policy','Placement Report','Admission Procedure',
    'Eligibility Criteria','Curriculum','Internship Details','Academic Calendar','FAQ','Hostel Info','Refund Policy','Parent Information Guide'];
  const statuses: KmsDoc['status'][] = ['Active','Active','Active','Under Approval','Needs Review','Processing','Active','Expired','Active','Approved'];
  const docs: KmsDoc[] = [];
  for (let i = 0; i < 26; i++) {
    const cat = pick(cats);
    const st = pick(statuses);
    docs.push({
      documentId: `doc-${(i + 1).toString().padStart(3, '0')}`,
      title: `${cat} — ${pick(COURSES)}`.slice(0, 52),
      description: `Official ${cat.toLowerCase()} for the 2026 admission cycle.`,
      category: cat,
      course: rnd() > 0.4 ? pick(COURSES) : undefined,
      academicYear: '2026–27',
      version: int(1, 4),
      status: st,
      uploadedBy: pick(['K. Iyer','M. Rao','Admin']),
      uploadedAt: isoDaysAgo(int(3, 90)),
      approvedBy: st === 'Active' || st === 'Approved' ? pick(['Compliance Office','S. Banerjee']) : undefined,
      effectiveDate: st === 'Active' ? isoDaysAgo(int(1, 40)) : undefined,
      expiryDate: st === 'Expired' ? isoDaysAgo(int(1, 10)) : (rnd() > 0.7 ? isoDaysAhead(int(5, 30)) : undefined),
      aiTrainingStatus: st === 'Active' ? 'Trained' : st === 'Expired' ? 'Excluded' : 'Queued',
      confidenceScore: int(62, 99),
      conflictScore: rnd() > 0.8 ? int(30, 70) : int(0, 12),
      usageCount: st === 'Active' ? int(20, 480) : int(0, 12),
      lastUsedAt: st === 'Active' ? isoDaysAgo(int(0, 4)) : undefined,
      tags: [cat.split(' ')[0].toLowerCase(), '2026'],
      sizeKb: int(120, 4200),
    });
  }
  return docs;
}

export function buildApprovals(): ApprovalRequest[] {
  resetSeed();
  const types = ['KMS document','Scholarship answer','Fee-related answer','Guardrail change','WhatsApp template','Email template','Voice script','Placement claim'];
  const list: ApprovalRequest[] = [];
  for (let i = 0; i < 9; i++) {
    const t = pick(types);
    list.push({
      requestId: `apr-${(i + 1).toString().padStart(3, '0')}`,
      title: `${t}: ${pick(['B.Tech AI scholarship rules','MBA fee components 2026','Placement statement wording','WhatsApp fee-reminder template','Hostel safety FAQ','Refund policy update'])}`,
      requestType: t,
      entityType: t.includes('document') ? 'KmsDoc' : t.includes('Guardrail') ? 'Guardrail' : 'Template',
      requestedBy: pick(['K. Iyer','M. Rao','Aisha (AI)','R. Desai']),
      status: pick<ApprovalRequest['status']>(['Submitted','Under Review','Under Review','Changes Requested']),
      riskLevel: pick(BANDS),
      aiImpact: pick(['Counselor will be able to answer scholarship eligibility questions.','Affects fee figures quoted across all channels.','Changes placement wording — high compliance sensitivity.','New template available for fee reminders.']),
      changeSummary: pick(['Adds merit scholarship cutoff (85%) and 40% tuition waiver.','Updates total fee to ₹6.4L incl. new lab component.','Rewords placement claim to remove implied guarantee.','New approved WhatsApp template for fee reminders.']),
      current: 'Up to 30% tuition waiver for merit candidates.',
      proposed: 'Up to 40% tuition waiver for candidates scoring 85%+ in 12th; need-based add-on up to 15%.',
      slaDueAt: isoDaysAhead(int(-1, 3)),
      createdAt: isoDaysAgo(int(0, 5)),
      step: pick(['Knowledge Manager','Compliance']),
    });
  }
  return list;
}

export function buildEscalations(cands: Candidate[]): Escalation[] {
  resetSeed();
  const reasons = ['Low AI confidence','Parent requested human','Fee negotiation','Scholarship exception','Sensitive question','Conflicting information','Payment issue','VIP reference','Repeated unanswered question'];
  const list: Escalation[] = [];
  for (let i = 0; i < 7; i++) {
    const c = cands[int(0, cands.length - 1)];
    const distress = i === 0 ? true : rnd() > 0.9;
    list.push({
      escalationId: `esc-${(i + 1).toString().padStart(3, '0')}`,
      candidateId: c.candidateId, candidateName: c.name,
      reason: distress ? 'Emotional distress signal detected' : pick(reasons),
      channel: pick<Channel>(['voice','whatsapp','email']),
      urgency: distress ? 'Critical' : pick(['High','Medium','High','Low']),
      sentiment: distress ? 'very-neg' : pick(SENTI),
      conversionProbability: c.conversionProbability,
      aiSummary: distress
        ? 'Candidate expressed significant stress about exam results and family pressure. Sales framing stopped; care response delivered. Needs human support immediately.'
        : `Candidate asked for a fee concession beyond approved scholarship rules. AI declined to commit and flagged for human review with full context.`,
      recommendedResponse: distress
        ? 'Acknowledge feelings, share counseling support resources, do not discuss admissions. Connect to a human counselor now.'
        : 'Confirm approved scholarship range, explain eligibility, offer a counselor call to discuss options.',
      assignedTo: rnd() > 0.6 ? pick(HUMANS) : undefined,
      slaDueAt: isoDaysAhead(0),
      status: rnd() > 0.7 ? 'Claimed' : 'Open',
      distress,
      createdAt: isoDaysAgo(0, int(0, 6)),
    });
  }
  // ensure the distress one is first & open & critical
  list.sort((a, b) => (b.distress ? 1 : 0) - (a.distress ? 1 : 0));
  return list;
}

export function buildApplications(cands: Candidate[]): Application[] {
  const appy = cands.filter(c => c.applicationStatus !== '—').slice(0, 14);
  return appy.map((c, i) => ({
    applicationId: `app-${(i + 1).toString().padStart(3, '0')}`,
    candidateId: c.candidateId, candidateName: c.name, course: c.preferredCourse,
    stage: c.currentStage,
    feeStatus: c.currentStage.includes('Fee Paid') || c.currentStage.includes('Submitted') || c.currentStage.includes('Admi') ? 'Paid'
      : c.currentStage.includes('Fee Pending') ? 'Pending' : 'Not started',
    submittedDocs: ['10th marksheet','12th marksheet'],
    missingDocs: c.currentStage.includes('Pending') ? ['ID proof','Photograph'] : [],
    nextAction: c.recommendedNextAction.label,
    highIntent: c.conversionProbability > 70,
  }));
}

export function buildReferences(): ReferenceProvider[] {
  resetSeed();
  return PROVIDERS.filter(p => p !== '—').map((name, i) => {
    const referred = int(40, 320);
    const contacted = Math.floor(referred * (0.7 + rnd() * 0.25));
    const interested = Math.floor(contacted * (0.4 + rnd() * 0.3));
    const registered = Math.floor(interested * (0.4 + rnd() * 0.3));
    const applied = Math.floor(registered * (0.5 + rnd() * 0.3));
    const admitted = Math.floor(applied * (0.4 + rnd() * 0.3));
    return {
      providerId: `ref-${i + 1}`, name, type: pick(['Agent','School Partner','Alumni','Campaign']),
      referred, contacted, interested, registered, applied, admitted,
      conversionPct: Math.round((admitted / referred) * 1000) / 10,
      qualityScore: int(48, 94),
      revenuePotential: admitted * int(400000, 700000),
    };
  }).sort((a, b) => b.conversionPct - a.conversionPct);
}

export function buildMetrics(): Metric[] {
  const spark = (base: number) => Array.from({ length: 12 }, (_, i) => Math.round(base * (0.6 + 0.5 * Math.sin(i / 2) + rnd() * 0.3)));
  resetSeed();
  return [
    { key: 'total-leads', label: 'Total leads', value: 4820, deltaPct: 12.4, trend: spark(4000), format: 'int', drillTo: '/app/crm' },
    { key: 'contacted', label: 'Candidates contacted', value: 3914, deltaPct: 8.1, trend: spark(3500), format: 'int', drillTo: '/app/crm' },
    { key: 'conversations', label: 'Successful conversations', value: 2188, deltaPct: 15.2, trend: spark(1800), format: 'int', tone: 'ai' },
    { key: 'registrations', label: 'Registrations', value: 612, deltaPct: 9.7, trend: spark(520), format: 'int', tone: 'success', drillTo: '/app/applications' },
    { key: 'apps-submitted', label: 'Applications submitted', value: 389, deltaPct: 6.3, trend: spark(340), format: 'int', tone: 'success', drillTo: '/app/applications' },
    { key: 'admissions', label: 'Admissions confirmed', value: 154, deltaPct: 18.9, trend: spark(120), format: 'int', tone: 'success' },
    { key: 'conversion', label: 'Conversion rate', value: 12.7, deltaPct: 2.1, trend: spark(11), format: 'pct', tone: 'success' },
    { key: 'high-intent', label: 'High-intent candidates', value: 487, deltaPct: 22.0, trend: spark(360), format: 'int', tone: 'ai' },
    { key: 'escalations', label: 'Pending escalations', value: 7, deltaPct: -14.0, trend: spark(10), format: 'int', tone: 'warning', drillTo: '/app/handoff' },
    { key: 'ai-confidence', label: 'AI confidence avg', value: 91, deltaPct: 1.4, trend: spark(88), format: 'pct', tone: 'ai' },
    { key: 'knowledge-gaps', label: 'Knowledge gaps', value: 7, deltaPct: -30.0, trend: spark(12), format: 'int', tone: 'warning', drillTo: '/app/learning-review' },
    { key: 'fee-paid', label: 'Application fees paid', value: 271, deltaPct: 11.0, trend: spark(230), format: 'int', tone: 'success' },
  ];
}

export function buildFunnel(): FunnelStage[] {
  return [
    { key: 'captured', label: 'Lead captured', count: 4820, dropOffPct: 0, trendPct: 12 },
    { key: 'contacted', label: 'Contacted', count: 3914, dropOffPct: 18.8, trendPct: 8 },
    { key: 'interested', label: 'Interested', count: 2188, dropOffPct: 44.1, trendPct: 15 },
    { key: 'parent', label: 'Parent discussion', count: 1342, dropOffPct: 38.7, trendPct: 6 },
    { key: 'registered', label: 'Registered', count: 612, dropOffPct: 54.4, trendPct: 10 },
    { key: 'app-started', label: 'Application started', count: 503, dropOffPct: 17.8, trendPct: 9 },
    { key: 'fee-paid', label: 'Fee paid', count: 271, dropOffPct: 46.1, trendPct: 11 },
    { key: 'submitted', label: 'Application submitted', count: 389, dropOffPct: -43.5, trendPct: 6 },
    { key: 'admitted', label: 'Admitted', count: 154, dropOffPct: 60.4, trendPct: 19 },
  ];
}

export function buildLeadSources(): BarDatum[] {
  return [
    { label: 'Website', value: 1420, sub: '14.2% conv' },
    { label: 'Education Fair', value: 980, sub: '9.1% conv' },
    { label: 'Social Media', value: 760, sub: '7.8% conv' },
    { label: 'School Partner', value: 640, sub: '16.4% conv' },
    { label: 'Webinar', value: 520, sub: '11.0% conv' },
    { label: 'Paid Ads', value: 500, sub: '5.4% conv' },
  ];
}
export function buildCourseDemand(): BarDatum[] {
  return [
    { label: 'B.Tech AI & DS', value: 1180 },
    { label: 'MBA', value: 940 },
    { label: 'B.Tech CSE', value: 880 },
    { label: 'B.Sc Data Science', value: 560 },
    { label: 'B.Des UX', value: 410 },
    { label: 'BBA', value: 360 },
  ];
}
export function buildProbabilityDist(): BarDatum[] {
  return [
    { label: 'Low (<40%)', value: 2140, tone: 'low' },
    { label: 'Medium (40–70%)', value: 1690, tone: 'med' },
    { label: 'High (>70%)', value: 990, tone: 'high' },
  ];
}

export function buildInsights(): InsightCard[] {
  return [
    { id: 'i1', scope: 'Hyderabad · Data Science', tone: 'ai', narrative: 'Candidates from Hyderabad are asking more about scholarships for Data Science programs — consider a targeted scholarship campaign.' },
    { id: 'i2', scope: 'Parent concerns', tone: 'warning', narrative: 'Parents are most concerned about placement assurance and hostel facilities. Approved placement docs reduce escalation rate by 23%.' },
    { id: 'i3', scope: 'Channel mix', tone: 'positive', narrative: 'WhatsApp follow-ups convert 1.8× better than email for high-intent candidates.' },
    { id: 'i4', scope: 'MBA program', tone: 'warning', narrative: 'MBA shows high interest but high drop-off at the fee-discussion stage — review fee messaging.' },
    { id: 'i5', scope: 'Knowledge gap', tone: 'ai', narrative: 'The counselor lacks approved information about internship partners for the B.Tech AI program.' },
  ];
}

export function buildActivity(cands: Candidate[]): ActivityItem[] {
  resetSeed();
  const verbs: [Channel, string][] = [
    ['voice', 'AI call connected — discussing course options'],
    ['whatsapp', 'WhatsApp reply received — asked about fees'],
    ['email', 'Scholarship email opened'],
    ['voice', 'AI counselor escalated to human (low confidence)'],
    ['whatsapp', 'Course brochure delivered & read'],
    ['vcon', 'V-Con confirmed for tomorrow 4:00 PM'],
    ['voice', 'High-intent candidate detected'],
    ['email', 'Application reminder sent'],
  ];
  return Array.from({ length: 14 }, (_, i) => {
    const c = cands[int(0, cands.length - 1)];
    const [channel, text] = pick(verbs);
    return { id: `act-${i}`, channel, actor: (rnd() > 0.3 ? 'ai' : 'human') as any, candidate: c.name, text, ts: isoDaysAgo(0, i), sentiment: pick(SENTI) };
  });
}

export function buildNotifications(): AppNotification[] {
  return [
    { id: 'n1', priority: 'Critical', title: 'Emotional-distress signal detected', body: 'A candidate conversation was flagged. Sales framing stopped; routed to human with high urgency.', ts: isoDaysAgo(0, 1), read: false, icon: 'alert-triangle', link: '/app/handoff' },
    { id: 'n2', priority: 'High', title: 'New high-intent candidate', body: 'Ananya Reddy reached 82% conversion probability after a V-Con offer.', ts: isoDaysAgo(0, 2), read: false, icon: 'flame', link: '/app/crm/candidate/cand-001' },
    { id: 'n3', priority: 'High', title: 'Document approval pending', body: 'B.Tech AI scholarship rules awaiting Compliance sign-off (SLA in 4h).', ts: isoDaysAgo(0, 3), read: false, icon: 'file-check', link: '/app/approvals' },
    { id: 'n4', priority: 'Medium', title: 'Knowledge gap detected', body: '7 questions need approved answers before the counselor can respond confidently.', ts: isoDaysAgo(0, 5), read: false, icon: 'brain', link: '/app/learning-review' },
    { id: 'n5', priority: 'Medium', title: 'CRM import completed', body: '118 of 130 rows imported. 12 rows need attention.', ts: isoDaysAgo(0, 8), read: true, icon: 'upload', link: '/app/crm/import' },
    { id: 'n6', priority: 'Low', title: 'Campaign completed', body: 'Spring 2026 — MBA WhatsApp campaign finished. 64% read rate.', ts: isoDaysAgo(1), read: true, icon: 'send', link: '/app/campaigns' },
  ];
}

/* =====================================================================
   Career Counselor (Vera) — dashboard data
   ===================================================================== */
export function buildCareerMetrics(): Metric[] {
  const spark = (base: number) => Array.from({ length: 12 }, (_, i) => Math.round(base * (0.6 + 0.5 * Math.sin(i / 2) + rnd() * 0.3)));
  resetSeed();
  return [
    { key: 'career-convos', label: 'Career conversations', value: 3142, deltaPct: 18.6, trend: spark(2500), format: 'int', tone: 'ai' },
    { key: 'profiled', label: 'Students profiled', value: 2410, deltaPct: 14.2, trend: spark(2000), format: 'int', drillTo: '/app/crm' },
    { key: 'pathways', label: 'Pathways recommended', value: 1876, deltaPct: 21.0, trend: spark(1500), format: 'int', tone: 'ai' },
    { key: 'skill-assess', label: 'Skill assessments', value: 1294, deltaPct: 9.4, trend: spark(1100), format: 'int' },
    { key: 'upskilling', label: 'Upskilling enrolments', value: 642, deltaPct: 27.3, trend: spark(480), format: 'int', tone: 'success' },
    { key: 'career-ready', label: 'Career-ready students', value: 538, deltaPct: 16.1, trend: spark(420), format: 'int', tone: 'success' },
    { key: 'placements', label: 'Placements influenced', value: 311, deltaPct: 22.8, trend: spark(240), format: 'int', tone: 'success' },
    { key: 'readiness-avg', label: 'Career-readiness avg', value: 74, deltaPct: 3.2, trend: spark(70), format: 'pct', tone: 'ai' },
    { key: 'mentor-matches', label: 'Mentor matches', value: 196, deltaPct: 11.5, trend: spark(150), format: 'int' },
    { key: 'aptitude', label: 'Aptitude tests taken', value: 1487, deltaPct: 8.0, trend: spark(1300), format: 'int' },
    { key: 'parent-career', label: 'Parent career talks', value: 873, deltaPct: 12.7, trend: spark(700), format: 'int' },
    { key: 'skill-gaps', label: 'Skill gaps flagged', value: 9, deltaPct: -18.0, trend: spark(13), format: 'int', tone: 'warning', drillTo: '/app/learning-review' },
  ];
}

export function buildCareerFunnel(): FunnelStage[] {
  return [
    { key: 'engaged', label: 'Engaged', count: 3142, dropOffPct: 0, trendPct: 18 },
    { key: 'interests', label: 'Interests profiled', count: 2410, dropOffPct: 23.3, trendPct: 14 },
    { key: 'aptitude', label: 'Aptitude assessed', count: 1487, dropOffPct: 38.3, trendPct: 8 },
    { key: 'pathway', label: 'Pathway recommended', count: 1876, dropOffPct: -26.2, trendPct: 21 },
    { key: 'skill-plan', label: 'Skill plan created', count: 1042, dropOffPct: 44.5, trendPct: 12 },
    { key: 'upskilling', label: 'Upskilling enrolled', count: 642, dropOffPct: 38.4, trendPct: 27 },
    { key: 'ready', label: 'Career-ready', count: 538, dropOffPct: 16.2, trendPct: 16 },
    { key: 'placed', label: 'Placed / progressed', count: 311, dropOffPct: 42.2, trendPct: 23 },
  ];
}

export function buildCareerInterests(): BarDatum[] {
  return [
    { label: 'Software Engineering', value: 980, sub: '78% readiness' },
    { label: 'Data Science & AI', value: 910, sub: '74% readiness' },
    { label: 'Product / UX Design', value: 540, sub: '69% readiness' },
    { label: 'Finance & Analytics', value: 470, sub: '66% readiness' },
    { label: 'Cybersecurity', value: 410, sub: '71% readiness' },
    { label: 'Healthcare & Bio', value: 360, sub: '63% readiness' },
  ];
}

export function buildCareerReadiness(): BarDatum[] {
  return [
    { label: 'Emerging (<50%)', value: 760, tone: 'low' },
    { label: 'Developing (50–75%)', value: 1340, tone: 'med' },
    { label: 'Career-ready (>75%)', value: 990, tone: 'high' },
  ];
}

export function buildCareerInsights(): InsightCard[] {
  return [
    { id: 'ci1', scope: 'Hyderabad · career interest', tone: 'ai', narrative: 'Data Science & AI roles are the top career interest among Hyderabad students — pair pathway guidance with the approved internship-partner list.' },
    { id: 'ci2', scope: 'Skill-gap impact', tone: 'positive', narrative: 'Students who complete an approved skill-gap plan are 2.1× more likely to reach career-ready status.' },
    { id: 'ci3', scope: 'Parents', tone: 'warning', narrative: 'Parents most often ask about long-term job stability and salary ranges — Vera answers only from approved placement data.' },
    { id: 'ci4', scope: 'Pathway → course', tone: 'ai', narrative: 'B.Des UX aspirants increasingly ask for portfolio mentorship; consider an approved mentor-match track.' },
    { id: 'ci5', scope: 'Knowledge gap', tone: 'warning', narrative: 'Vera lacks approved salary-band data for emerging AI roles — flagged for the Knowledge Manager.' },
  ];
}
