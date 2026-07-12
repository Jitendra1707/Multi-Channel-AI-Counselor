/* =====================================================================
   Admission Counsellor — Domain Models  (Blueprint §37)
   Conceptual models powering the prototype against mocked data.
   ===================================================================== */

export type Role =
  | 'super-admin' | 'institution-admin' | 'admission-director' | 'admission-manager'
  | 'ai-supervisor' | 'knowledge-manager' | 'compliance-officer' | 'crm-manager'
  | 'human-counselor' | 'management-viewer';

export type Channel = 'voice' | 'whatsapp' | 'email' | 'vcon' | 'web' | 'note';
export type Direction = 'in' | 'out';
export type Actor = 'ai' | 'human';

export type Sentiment = 'very-neg' | 'neg' | 'neutral' | 'pos' | 'very-pos';
export type Band = 'low' | 'med' | 'high';

/** Candidate lifecycle (§13.2) */
export type CandidateStage =
  | 'New Lead' | 'Imported' | 'Validated' | 'Contact Pending' | 'Contacted'
  | 'Interested' | 'Not Interested' | 'Needs More Information'
  | 'Parent Discussion Required' | 'V-Con Scheduled' | 'Counseling Completed'
  | 'Registration Link Sent' | 'Registered' | 'Application Started'
  | 'Application Fee Pending' | 'Application Fee Paid' | 'Application Submitted'
  | 'Admission Offered' | 'Admitted' | 'Deferred' | 'Lost' | 'Disqualified'
  // BusinessLayer lead statuses, shown verbatim (polished) on real CRM leads.
  | 'New' | 'Welcomed' | 'Scheduling' | 'Scheduled' | 'In Call' | 'Called'
  | 'Follow-up' | 'Escalated' | 'Delegated' | 'Converted' | 'Closed';

export interface Consent {
  call: boolean;
  whatsapp: boolean;
  email: boolean;
  recording: boolean;
  capturedAt?: string;
  source?: string;
}

export interface Parent {
  parentId: string;
  candidateId: string;
  name: string;
  relationship: 'Father' | 'Mother' | 'Guardian';
  mobile?: string;
  whatsapp?: string;
  email?: string;
  preferredLanguage: string;
  concerns: string[];
  sentiment: Sentiment;
  lastContacted?: string;
  consentToDiscuss: boolean;
}

export interface Candidate {
  candidateId: string;
  name: string;
  avatarHue: number;          // for generated avatar color
  mobile: string;
  whatsapp: string;
  email: string;
  city: string;
  region: string;
  country: string;
  academicBackground: string;
  careerInterests: string[];
  preferredCourse: string;
  budgetRange: string;
  budgetSensitivity: Band;
  scholarshipInterest: boolean;
  parents: Parent[];
  consent: Consent;
  leadSource: string;
  referenceProvider?: string;
  currentStage: CandidateStage;
  conversionProbability: number;   // 0..100
  dropOffRisk: Band;
  sentiment: Sentiment;
  assignedAiCounselor: string;
  assignedHumanCounselor?: string;
  parentEngagement: 'None' | 'Pending' | 'Engaged' | 'Concerns Raised';
  applicationStatus: string;
  /** Admissions LIFECYCLE stage (backend funnel_stage): raw | lead |
   *  application_started | fees_pending | application_submitted. Shown in the
   *  CRM "Stage" column. SEPARATE from currentStage (the operational status). */
  funnelStage?: string;
  /** Lead TEMPERATURE (backend lead_priority): 'hot' | 'warm' | 'cold'. Null
   *  until the lead has been analyzed. Cosmetic/prioritisation only. */
  leadPriority?: string | null;
  tags: string[];
  duplicate?: boolean;
  doNotContact?: boolean;
  lastContacted: string;
  nextFollowUp?: string;
  createdBy: string;
  createdAt: string;
  pendingQuestions: string[];
  lastAiSummary: string;
  recommendedNextAction: { label: string; channel: Channel; reason: string };
  /** Documents/items actually delivered to the lead (backend lead.sent_items). */
  sentItems?: { item: string; channel: string; at: string }[];
  /** True when sourced from the real backend (BusinessLayer); gates Group-B
   *  placeholder fields on the integrated CRM/profile screens. */
  backed?: boolean;
}

export interface CommEvent {
  eventId: string;
  candidateId: string;
  channel: Channel;
  direction: Direction;
  timestamp: string;
  status: string;
  summary: string;
  body?: string;
  sentiment: Sentiment;
  intent?: string;
  aiConfidence?: number;       // 0..100
  outcome?: string;
  followUpAction?: string;
  humanHandoffFlag?: boolean;
  actor: Actor;
  durationSec?: number;
  probabilityDelta?: number;
  docsShared?: string[];
}

export interface ChatMessage {
  id: string;
  author: 'ai' | 'human' | 'candidate' | 'parent';
  text: string;
  ts: string;
  status?: 'sending' | 'sent' | 'delivered' | 'read' | 'failed';
  kind?: 'text' | 'course-card' | 'fee-card' | 'scholarship-card' | 'link' | 'voice-note';
  cardTitle?: string;
  cardMeta?: string;
}

/* ----------  Journey  ---------- */
export interface JourneyEvent {
  id: string;
  type: string;
  label: string;
  channel: Channel | 'system';
  owner: Actor | 'system';
  ts: string;
  summary: string;
  sentiment?: Sentiment;
  probabilityDelta?: number;
  docsShared?: string[];
}

/* ----------  KMS  ---------- */
export type DocStatus =
  | 'Draft' | 'Uploaded' | 'Processing' | 'Extracted' | 'Needs Review'
  | 'Under Approval' | 'Approved' | 'Active' | 'Rejected' | 'Archived'
  | 'Expired' | 'Deletion Requested' | 'Unlearn Pending';

export interface KmsDoc {
  documentId: string;
  title: string;
  description: string;
  category: string;
  course?: string;
  academicYear: string;
  version: number;
  status: DocStatus;
  uploadedBy: string;
  uploadedAt: string;
  approvedBy?: string;
  effectiveDate?: string;
  expiryDate?: string;
  aiTrainingStatus: 'Not started' | 'Queued' | 'Trained' | 'Excluded';
  confidenceScore: number;   // 0..100
  conflictScore: number;     // 0..100 (lower better)
  usageCount: number;
  lastUsedAt?: string;
  tags: string[];
  sizeKb: number;
}

/* ----------  Approvals  ---------- */
export type ApprovalStatus = 'Submitted' | 'Under Review' | 'Changes Requested' | 'Approved' | 'Rejected';
export interface ApprovalRequest {
  requestId: string;
  title: string;
  requestType: string;
  entityType: string;
  requestedBy: string;
  status: ApprovalStatus;
  riskLevel: Band;
  aiImpact: string;
  changeSummary: string;
  current?: string;
  proposed?: string;
  slaDueAt: string;
  createdAt: string;
  step: 'Knowledge Manager' | 'Compliance' | 'Done';
}

/* ----------  Handoff  ---------- */
export interface Escalation {
  escalationId: string;
  candidateId: string;
  candidateName: string;
  reason: string;
  channel: Channel;
  urgency: 'Critical' | 'High' | 'Medium' | 'Low';
  sentiment: Sentiment;
  conversionProbability: number;
  aiSummary: string;
  recommendedResponse: string;
  assignedTo?: string;
  slaDueAt: string;
  status: 'Open' | 'Claimed' | 'Resolved' | 'Returned';
  distress?: boolean;
  createdAt: string;
}

/* ----------  Applications  ---------- */
export interface Application {
  applicationId: string;
  candidateId: string;
  candidateName: string;
  course: string;
  stage: string;
  feeStatus: 'Not started' | 'Pending' | 'Paid';
  submittedDocs: string[];
  missingDocs: string[];
  nextAction: string;
  highIntent: boolean;
}

/* ----------  References  ---------- */
export interface ReferenceProvider {
  providerId: string;
  name: string;
  type: string;
  referred: number;
  contacted: number;
  interested: number;
  registered: number;
  applied: number;
  admitted: number;
  conversionPct: number;
  qualityScore: number;     // 0..100
  revenuePotential: number; // currency
}

/* ----------  Dashboard primitives  ---------- */
export interface Metric {
  key: string;
  label: string;
  value: number;
  display?: string;
  deltaPct: number;
  trend: number[];
  format?: 'int' | 'pct' | 'currency';
  channel?: Channel;
  tone?: 'default' | 'success' | 'warning' | 'danger' | 'ai';
  drillTo?: string;
}

export interface FunnelStage {
  key: string;
  label: string;
  count: number;
  dropOffPct: number;
  trendPct: number;
}

export interface BarDatum { label: string; value: number; sub?: string; tone?: string; }

export interface InsightCard {
  id: string;
  narrative: string;
  scope: string;
  tone: 'default' | 'positive' | 'warning' | 'ai';
}

export interface ActivityItem {
  id: string;
  channel: Channel;
  actor: Actor;
  candidate: string;
  text: string;
  ts: string;
  sentiment?: Sentiment;
}

/* ----------  AI Counselor config  ---------- */
export type FieldApproval = 'approved' | 'pending' | 'draft';
export interface BehaviorField {
  key: string;
  label: string;
  value: string;
  approval: FieldApproval;
  claimBearing?: boolean;
}
export interface ChannelStatus {
  channel: Channel;
  status: 'live' | 'limited' | 'paused' | 'blocked';
  reason?: string;
}

/* ----------  Notifications  ---------- */
export interface AppNotification {
  id: string;
  priority: 'Critical' | 'High' | 'Medium' | 'Low' | 'Informational';
  title: string;
  body: string;
  ts: string;
  read: boolean;
  icon: string;
  link?: string;
}

/* ----------  Auth / tenant  ---------- */
export interface CurrentUser {
  userId: string;
  name: string;
  email: string;
  role: Role;
  roleLabel: string;
  initials: string;
  hue: number;
}
export interface Institution {
  institutionId: string;
  name: string;
  shortName: string;
  type: string;
}
