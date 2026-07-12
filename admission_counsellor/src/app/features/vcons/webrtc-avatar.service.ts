import { Injectable, signal } from '@angular/core';
import { environment } from '../../../environments/environment';
import { UiDirective, parseUiDirective } from './ui-directive';

/**
 * WebrtcAvatarService — drives the browser ↔ AegisBackend WebRTC connection for
 * the avatar (Simli / SoulX behind Pipecat's SmallWebRTC transport). Angular port
 * of web-app/src/hooks/useWebRTCAvatar.ts.
 *
 * Flow (matches pipecat.transports.smallwebrtc):
 *   1. getUserMedia(mic) → RTCPeerConnection (STUN).
 *   2. Add mic audio track (sendrecv) + video recvonly transceiver so the
 *      backend's answer can attach the avatar A/V.
 *   3. Create a data channel — REQUIRED so the backend can send "renegotiate"
 *      signalling (its send_app_message no-ops without a browser data channel).
 *   4. createOffer → ICE gather → POST /offer → setRemoteDescription(answer).
 *   5. Periodic "ping" over the data channel (backend keep-alive).
 *   6. On "renegotiate" → fresh offer with the same pc_id; apply the new answer.
 *   7. ontrack → collect remote tracks into one MediaStream on the <video>.
 *
 * Reactive state is exposed as signals. WebRTC callbacks fire OUTSIDE Angular's
 * zone, but signal writes notify change detection directly, so OnPush consumers
 * update correctly without NgZone.run.
 *
 * Provided at the component level (VconsComponent providers) so each call gets a
 * fresh instance, torn down with the screen.
 */

const PING_INTERVAL_MS = 1000;
const ICE_TIMEOUT_MS = 3000;
const API_BASE = environment.aegisUrl.replace(/\/$/, '');

export type CallStatus = 'idle' | 'connecting' | 'connected' | 'ended' | 'error';

export interface ChatAttachment {
  name: string;
  /** Bytes (for the size label). */
  size: number;
  /** MIME type, e.g. application/pdf. */
  mime: string;
  /** Object URL for local preview/download (revoked on teardown). */
  url: string;
}

export interface TranscriptEntry {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  /** Source of the turn — 'voice' (spoken, via STT) or 'chat' (typed/uploaded).
   *  Lets the chat window badge typed messages distinctly from spoken ones. */
  via?: 'voice' | 'chat';
  /** Present when this message is a document the user shared in chat. */
  attachment?: ChatAttachment;
}

interface OfferResponse {
  sdp: string;
  type: string;
  pc_id: string;
}

/** One conflicting KB passage the new fact clashes with (from the backend conflict analysis). */
export interface KnowledgeConflictItem {
  point_id: string | null;
  source_doc: string;
  version: string;
  relation: 'contradicts' | 'updates' | string;
  confidence: number;
  attribute: string;
  old_value: string;
  new_value: string;
  span: string;
  explanation: string;
}

export interface KnowledgeConflict {
  score: number;
  blocking: boolean;
  items: KnowledgeConflictItem[];
}

/** A fact captured from the director's speech — shown in-call as a READ-ONLY
 *  card; all actions happen on the Knowledge Review screen. `status` flips in
 *  place (pending → approved/superseded/rejected) via `knowledge_resolved`. */
export interface KnowledgeCandidate {
  id: string;
  conversation_id?: string;
  text: string;
  heading: string;
  topic: string;
  suggested_kb: string;
  confidence: number;
  status: string;
  version: number;
  source_span?: string;
  conflict: KnowledgeConflict;
}

/** Arm-first capture flow: idle → armed (say the fact now) → processing → idle. */
export type KnowledgeCaptureState = 'idle' | 'armed' | 'processing';

interface StartOptions {
  video: HTMLVideoElement;
  leadId?: string;
  /** Capture the director's camera for a local self-view PiP (default true). */
  camera?: boolean;
}

@Injectable()
export class WebrtcAvatarService {
  // ── Public reactive state (read-only views) ─────────────────────────
  private readonly _status = signal<CallStatus>('idle');
  private readonly _error = signal<string | null>(null);
  private readonly _muted = signal(false);
  private readonly _cameraOn = signal(false);
  private readonly _selfView = signal<MediaStream | null>(null);
  private readonly _transcript = signal<TranscriptEntry[]>([]);
  private readonly _uiDirective = signal<UiDirective | null>(null);
  private readonly _knowledgeCandidates = signal<KnowledgeCandidate[]>([]);
  private readonly _knowledgeCaptureState = signal<KnowledgeCaptureState>('idle');
  private readonly _knowledgeCaptureError = signal<string | null>(null);

  readonly status = this._status.asReadonly();
  readonly error = this._error.asReadonly();
  readonly muted = this._muted.asReadonly();
  /** Whether the director's camera is currently sending video to the self-view. */
  readonly cameraOn = this._cameraOn.asReadonly();
  /** Local camera stream for the director self-view PiP (never sent to the backend). */
  readonly selfView = this._selfView.asReadonly();
  readonly transcript = this._transcript.asReadonly();
  readonly uiDirective = this._uiDirective.asReadonly();
  /** Captured knowledge results shown as read-only in-call cards. */
  readonly knowledgeCandidates = this._knowledgeCandidates.asReadonly();
  /** Where the arm-first capture flow currently is (drives button + chips). */
  readonly knowledgeCaptureState = this._knowledgeCaptureState.asReadonly();
  /** Transient capture failure message (auto-clears). */
  readonly knowledgeCaptureError = this._knowledgeCaptureError.asReadonly();

  // ── Internals ───────────────────────────────────────────────────────
  private pc: RTCPeerConnection | null = null;
  private localStream: MediaStream | null = null;
  private dc: RTCDataChannel | null = null;
  private pcId: string | null = null;
  private videoEl: HTMLVideoElement | null = null;
  private leadId?: string;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  /** A/V-sync diagnostics probe timer (environment.avatarAvsyncDebug). */
  private avsyncTimer: ReturnType<typeof setInterval> | null = null;
  /** Previous jitter-buffer samples per inbound-rtp report id (for per-frame deltas).
   *  `target` tracks jitterBufferTargetDelay so we can derive the browser's chosen
   *  target (NetEQ may inflate it past our hint under bursty arrival). */
  private avsyncPrev = new Map<string, { delay: number; emitted: number; target: number }>();
  private transcriptId = 0;
  private cancelled = false;
  private negotiating = false;
  /** Object URLs created for chat attachments — revoked on teardown. */
  private attachmentUrls: string[] = [];
  /** Local failsafe so a lost `disarmed` message can't leave the button stuck. */
  private armFailsafeTimer: ReturnType<typeof setTimeout> | null = null;
  private captureErrorTimer: ReturnType<typeof setTimeout> | null = null;

  // ── Start (user-gesture initiated) ──────────────────────────────────
  async start(opts: StartOptions): Promise<void> {
    this.cancelled = false;
    this.videoEl = opts.video;
    this.leadId = opts.leadId;
    // Fresh call — clear any state left over from a previous session.
    this.attachmentUrls.forEach(u => URL.revokeObjectURL(u));
    this.attachmentUrls = [];
    this._transcript.set([]);
    this._uiDirective.set(null);
    this._knowledgeCandidates.set([]);
    this.setCaptureState('idle');
    this._knowledgeCaptureError.set(null);
    this._muted.set(false);
    this._cameraOn.set(false);
    this._selfView.set(null);
    this.transcriptId = 0;
    this._status.set('connecting');
    this._error.set(null);

    const wantCamera = opts.camera !== false;

    try {
      // 1. Microphone (required) + the director's camera (optional, for the
      //    local self-view PiP only — it is NOT added to the peer connection).
      const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
      let localStream: MediaStream;
      try {
        localStream = await navigator.mediaDevices.getUserMedia({ audio, video: wantCamera });
      } catch (camErr) {
        // Camera unavailable/denied — fall back to mic-only so the briefing can still start.
        if (!wantCamera) throw camErr;
        localStream = await navigator.mediaDevices.getUserMedia({ audio, video: false });
      }
      if (this.cancelled) {
        localStream.getTracks().forEach(t => t.stop());
        return;
      }
      this.localStream = localStream;

      // Local self-view (director camera) — kept local, never sent to the backend.
      const camTrack = localStream.getVideoTracks()[0];
      if (camTrack) {
        this._selfView.set(new MediaStream([camTrack]));
        this._cameraOn.set(true);
      }

      // 2. Peer connection
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
      });
      this.pc = pc;

      // Remote avatar media → single stream on the <video> (carries BOTH the
      // avatar video AND its audio, so the element must NOT be muted).
      const remoteStream = new MediaStream();
      if (this.videoEl) {
        this.videoEl.srcObject = remoteStream;
        this.videoEl.muted = false;
        this.videoEl.volume = 1.0;
      }
      pc.addEventListener('track', (e) => {
        remoteStream.addTrack(e.track);

        // Pin BOTH receivers' jitter buffers to the SAME target. The browser keeps
        // A/V in sync by matching buffer depths; pinning only audio (as before) let
        // the video buffer float and inflate under bursty delivery, forcing audio
        // early while video sat high (video-lags-audio). Equal targets bound the
        // offset toward zero. ~200ms absorbs the Simli/SoulX→backend→browser jitter
        // (matches HeyGen's default — inaudible in conversation). Tunable via env.
        if (e.track.kind === 'audio' || e.track.kind === 'video') {
          try {
            const targetMs = environment.avatarJitterBufferMs ?? 200;
            const r = e.receiver as unknown as {
              jitterBufferTarget?: number;
              playoutDelayHint?: number;
            };
            if ('jitterBufferTarget' in r) r.jitterBufferTarget = targetMs;
            else if ('playoutDelayHint' in r) r.playoutDelayHint = targetMs / 1000;
          } catch {
            /* receiver tuning unsupported here — ignore */
          }
        }

        const vid = this.videoEl;
        if (vid) {
          if (vid.srcObject !== remoteStream) vid.srcObject = remoteStream;
          // Runs within the user-initiated call flow → autoplay-with-sound is allowed.
          vid.play().catch(() => undefined);
        }
      });

      // 3. Transceivers — audio (sendrecv, mic) first, then video (recvonly).
      pc.addTrack(localStream.getAudioTracks()[0], localStream);
      pc.addTransceiver('video', { direction: 'recvonly' });

      // 4. Data channel (required for backend signalling / renegotiation).
      const dc = pc.createDataChannel('pipecat');
      this.dc = dc;
      dc.addEventListener('open', () => {
        this.pingTimer = setInterval(() => {
          if (dc.readyState === 'open') dc.send('ping');
        }, PING_INTERVAL_MS);
      });
      dc.addEventListener('message', (ev) => this.onDataChannelMessage(ev));

      // 5. Connection state → UI status
      pc.addEventListener('connectionstatechange', () => {
        const st = pc.connectionState;
        if (st === 'connected') {
          if (!this.cancelled) this._status.set('connected');
        } else if (st === 'failed' || st === 'closed') {
          if (!this.cancelled) this._status.set('error');
        }
      });

      // 6. Initial negotiation
      await this.negotiate();

      // 7. A/V-sync diagnostics probe (observe-only; environment.avatarAvsyncDebug).
      this.startAvsyncProbe(pc);
    } catch (err) {
      if (this.cancelled) return;
      this._error.set(err instanceof Error ? err.message : 'Failed to start the call');
      this._status.set('error');
    }
  }

  // ── Negotiate (initial + renegotiation share this) ──────────────────
  private async negotiate(): Promise<void> {
    const pc = this.pc;
    if (!pc || this.negotiating) return;
    this.negotiating = true;
    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await this.waitForIceGathering(pc);
      if (this.cancelled) return;

      const answer = await this.postOffer({
        sdp: pc.localDescription!.sdp,
        type: pc.localDescription!.type,
        pc_id: this.pcId ?? undefined,
        lead_id: this.leadId,
      });

      if (this.cancelled) {
        // Started → navigated away while postOffer() was in flight: the backend
        // already created the session. Delete it so it doesn't orphan.
        this.deleteSession(answer.pc_id);
        return;
      }

      this.pcId = answer.pc_id;
      await pc.setRemoteDescription(
        new RTCSessionDescription({ sdp: answer.sdp, type: answer.type as RTCSdpType }),
      );
    } finally {
      this.negotiating = false;
    }
  }

  // ── A/V-sync diagnostics probe (observe-only) ───────────────────────
  /**
   * Poll pc.getStats() at ~1Hz and log a single [avatar-avsync] line per tick
   * so we can attribute intermittent lip-sync lag to a layer:
   *   - audio_buf_ms / video_buf_ms = per-frame jitter-buffer depth
   *     (Δ jitterBufferDelay / Δ jitterBufferEmittedCount). delta ≈ the lip-sync
   *     offset the user sees. Video >> audio with ~zero loss ⇒ our untuned video
   *     buffer; video buffer high WITH loss/nack ⇒ network; freezes with delta≈0
   *     ⇒ symmetric network jitter (stays in sync).
   *   - video fps/frames/drops/freezes/nack/pli, per-track jitter+packetsLost,
   *     and connection RTT / available bitrate round out the attribution.
   * Gated by environment.avatarAvsyncDebug; zero behavior change.
   */
  private startAvsyncProbe(pc: RTCPeerConnection): void {
    if (!environment.avatarAvsyncDebug) return;
    this.avsyncPrev.clear();
    this.avsyncTimer = setInterval(() => {
      pc.getStats()
        .then((report) => {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const byKind: Record<string, any> = {};
          let rttMs: number | null = null;
          let availKbps: number | null = null;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          report.forEach((s: any) => {
            if (s.type === 'candidate-pair' && (s.nominated || s.selected)) {
              if (typeof s.currentRoundTripTime === 'number') rttMs = Math.round(s.currentRoundTripTime * 1000);
              if (typeof s.availableIncomingBitrate === 'number') availKbps = Math.round(s.availableIncomingBitrate / 1000);
            }
            if (s.type !== 'inbound-rtp') return;
            const kind: string = s.kind ?? s.mediaType ?? 'unknown';
            let bufMs: number | null = null;
            let targetMs: number | null = null;
            if (typeof s.jitterBufferDelay === 'number' && typeof s.jitterBufferEmittedCount === 'number') {
              const prev = this.avsyncPrev.get(s.id);
              const target = typeof s.jitterBufferTargetDelay === 'number' ? s.jitterBufferTargetDelay : 0;
              if (prev) {
                const dDelay = s.jitterBufferDelay - prev.delay;
                const dEmit = s.jitterBufferEmittedCount - prev.emitted;
                if (dEmit > 0) {
                  bufMs = Math.round((dDelay / dEmit) * 1000);
                  // Browser's CHOSEN target over the same emitted window — if this
                  // balloons with bufMs, NetEQ inflated the target itself (burst-driven),
                  // not our 200ms hint.
                  targetMs = Math.round(((target - prev.target) / dEmit) * 1000);
                }
              }
              this.avsyncPrev.set(s.id, {
                delay: s.jitterBufferDelay,
                emitted: s.jitterBufferEmittedCount,
                target,
              });
            }
            byKind[kind] = { s, bufMs, targetMs };
          });
          const a = byKind['audio'];
          const v = byKind['video'];
          const aBuf = a?.bufMs ?? null;
          const vBuf = v?.bufMs ?? null;
          const delta = aBuf !== null && vBuf !== null ? vBuf - aBuf : null;
          // eslint-disable-next-line no-console
          console.info('[avatar-avsync]', JSON.stringify({
            audio_buf_ms: aBuf,
            video_buf_ms: vBuf,
            av_buffer_delta_ms: delta,
            // NetEQ confirmation fields (Stage 0.5): target ballooning with audio_buf_ms
            // ⇒ NetEQ inflated its own target on bursty arrival; accel/decel rising ⇒
            // it's stretching/compressing audio to resync; concealed≈0 ⇒ no loss.
            audio_target_ms: a?.targetMs ?? null,
            audio_accel_samples: a?.s.removedSamplesForAcceleration ?? null,
            audio_decel_samples: a?.s.insertedSamplesForDeceleration ?? null,
            audio_concealed_samples: a?.s.concealedSamples ?? null,
            audio_pkts_recv: a?.s.packetsReceived ?? null,
            audio_jitter_ms: a ? Math.round((a.s.jitter ?? 0) * 1000) : null,
            video_jitter_ms: v ? Math.round((v.s.jitter ?? 0) * 1000) : null,
            audio_packets_lost: a?.s.packetsLost ?? null,
            video_packets_lost: v?.s.packetsLost ?? null,
            video_fps: v?.s.framesPerSecond ?? null,
            video_frames_decoded: v?.s.framesDecoded ?? null,
            video_frames_dropped: v?.s.framesDropped ?? null,
            video_freeze_count: v?.s.freezeCount ?? null,
            video_total_freezes_s: v?.s.totalFreezesDuration ?? null,
            video_pause_count: v?.s.pauseCount ?? null,
            video_nack_count: v?.s.nackCount ?? null,
            video_pli_count: v?.s.pliCount ?? null,
            rtt_ms: rttMs,
            avail_in_kbps: availKbps,
          }));
        })
        .catch(() => undefined);
    }, 1000);
  }

  private onDataChannelMessage(ev: MessageEvent): void {
    try {
      if (typeof ev.data === 'string' && ev.data.startsWith('ping')) return;
      const msg = JSON.parse(ev.data);
      if (msg?.type === 'transcript' && typeof msg.text === 'string') {
        const role: 'user' | 'assistant' = msg.role === 'user' ? 'user' : 'assistant';
        this._transcript.update(prev => [...prev, { id: this.transcriptId++, role, text: msg.text, via: 'voice' }]);
        return;
      }
      if (msg?.type === 'ui_directive') {
        const dir = parseUiDirective(msg.directive);
        if (dir) this._uiDirective.set(dir);
        return;
      }
      if (msg?.type === 'knowledge_capture_status' && typeof msg.state === 'string') {
        // Arm-first capture lifecycle: armed → processing → (candidate | failed).
        if (msg.state === 'armed') this.setCaptureState('armed');
        else if (msg.state === 'processing') this.setCaptureState('processing');
        else if (msg.state === 'disarmed') this.setCaptureState('idle');
        else if (msg.state === 'failed') {
          this.setCaptureState('idle');
          this.flashCaptureError(
            msg.reason === 'no_fact'
              ? 'I couldn’t make a clear fact out of that — press Capture and say it again.'
              : 'Something went wrong while capturing — please try again.',
          );
        }
        return;
      }
      if (msg?.type === 'knowledge_candidate' && typeof msg.id === 'string') {
        // Upsert by id (a post-call edit re-emits the same id with a fresh conflict block).
        const cand = msg as KnowledgeCandidate;
        this.setCaptureState('idle');
        this._knowledgeCandidates.update(prev => {
          const rest = prev.filter(c => c.id !== cand.id);
          return [...rest, cand];
        });
        return;
      }
      if (msg?.type === 'knowledge_resolved' && typeof msg.id === 'string') {
        // Resolved (possibly from the Knowledge Review screen while the call is
        // live) → flip the read-only card's status chip in place.
        this._knowledgeCandidates.update(prev =>
          prev.map(c => (c.id === msg.id ? { ...c, status: String(msg.status ?? c.status) } : c)),
        );
        return;
      }
      if (msg?.type === 'signalling' && msg?.message?.type === 'renegotiate') {
        void this.negotiate();
      }
    } catch {
      /* non-JSON keep-alive — ignore */
    }
  }

  // ── Mute toggle ─────────────────────────────────────────────────────
  toggleMute(): void {
    const track = this.localStream?.getAudioTracks()[0];
    if (!track) return;
    const nowMuted = track.enabled; // about to flip to !enabled
    track.enabled = !track.enabled;
    this._muted.set(!track.enabled);
    // Tell the backend to drop STT input while muted — track.enabled=false sends
    // digital silence, but a server-authoritative flag guarantees no transcription.
    try {
      if (this.dc && this.dc.readyState === 'open') {
        this.dc.send(JSON.stringify({ type: 'mute', muted: nowMuted }));
      }
    } catch {
      /* data channel not ready — local track.enabled=false still mutes */
    }
  }

  // ── Chat (typed text + document uploads) ────────────────────────────
  /**
   * Send a typed chat message. Echoes locally into the conversation thread as a
   * user bubble (so it shows immediately, Teams/Meet style) and forwards the
   * text to the backend over the data channel as a {type:'chat'} message — the
   * agent treats it the same as a spoken turn. Best-effort: the local echo is
   * never blocked on the data channel being open.
   */
  sendChatMessage(text: string): void {
    const trimmed = text.trim();
    if (!trimmed) return;
    this._transcript.update(prev => [
      ...prev,
      { id: this.transcriptId++, role: 'user', text: trimmed, via: 'chat' },
    ]);
    try {
      if (this.dc && this.dc.readyState === 'open') {
        this.dc.send(JSON.stringify({ type: 'chat', text: trimmed }));
        return; // real backend connected — its reply comes back over the channel
      }
    } catch {
      /* data channel not ready — fall through to the local demo reply */
    }
    // No backend wired yet: simulate Aisha's reply locally so the chat feels
    // live in a demo. This branch goes away on its own once the data channel
    // is open (the early-return above), so there's never a double reply.
    this.simulateReply(this.demoTextReply(trimmed));
  }

  /**
   * Share a document in chat. Adds a file card to the conversation thread and
   * notifies the backend (metadata only over the data channel — the bytes are
   * NOT streamed here). Returns the created entry's attachment for previewing.
   */
  sendAttachment(file: File): void {
    const url = URL.createObjectURL(file);
    this.attachmentUrls.push(url);
    const attachment: ChatAttachment = {
      name: file.name,
      size: file.size,
      mime: file.type || 'application/octet-stream',
      url,
    };
    this._transcript.update(prev => [
      ...prev,
      { id: this.transcriptId++, role: 'user', text: '', via: 'chat', attachment },
    ]);
    try {
      if (this.dc && this.dc.readyState === 'open') {
        this.dc.send(JSON.stringify({
          type: 'attachment',
          name: attachment.name, size: attachment.size, mime: attachment.mime,
        }));
        return; // real backend connected — it will acknowledge the upload
      }
    } catch {
      /* data channel not ready — fall through to the local demo acknowledgement */
    }
    // No backend wired yet: acknowledge the upload locally so it feels received.
    this.simulateReply(
      `Got it — I've received "${attachment.name}". Once my document tools are ` +
      `connected I'll read through it and we can discuss it here.`,
    );
  }

  // ── Demo-only assistant replies (front-end, no backend) ─────────────
  /** Push an assistant bubble after a short, natural delay (typing pause). */
  private simulateReply(text: string): void {
    const delay = 600 + Math.min(1400, text.length * 12);
    setTimeout(() => {
      if (this.cancelled) return;
      this._transcript.update(prev => [
        ...prev,
        { id: this.transcriptId++, role: 'assistant', text, via: 'chat' },
      ]);
    }, delay);
  }

  /** A light, on-persona canned reply so typed chat feels alive in a demo. */
  private demoTextReply(userText: string): string {
    const t = userText.toLowerCase();
    if (/\b(hi|hello|hey|good (morning|afternoon|evening))\b/.test(t)) {
      return 'Hi! I’m Aisha. Ask me anything about the outreach briefing, or share a document and I’ll take a look.';
    }
    if (t.includes('?')) {
      return 'Good question. Once my analytics and knowledge tools are connected I’ll answer that with the approved figures — for now this is a preview of the chat experience.';
    }
    if (/\b(thanks|thank you|cheers)\b/.test(t)) {
      return 'Anytime! Anything else you’d like to go over?';
    }
    return 'Got it. This chat is a front-end preview — when my backend is wired I’ll respond here with real answers, just like the spoken conversation.';
  }

  /** Toggle the director's self-view camera (local only — not sent to the backend). */
  toggleCamera(): void {
    const track = this.localStream?.getVideoTracks()[0];
    if (!track) return;
    track.enabled = !track.enabled;
    this._cameraOn.set(track.enabled);
  }

  /** Dismiss the on-screen report → back to the normal avatar stage. */
  clearUiDirective(): void {
    this._uiDirective.set(null);
  }

  // ── Knowledge capture (director states a new fact) ──────────────────
  private sendJson(payload: Record<string, unknown>): boolean {
    try {
      if (this.dc && this.dc.readyState === 'open') {
        this.dc.send(JSON.stringify(payload));
        return true;
      }
    } catch {
      /* data channel not ready */
    }
    return false;
  }

  /** Arm-first capture toggle. idle → arm (the NEXT statement is the fact);
   *  armed → cancel; processing → no-op (duplicate-click guard). The result
   *  arrives as a read-only `knowledge_candidate` card; all actions happen on
   *  the Knowledge Review screen. */
  toggleKnowledgeCapture(): void {
    const st = this._knowledgeCaptureState();
    if (st === 'processing' || this._status() !== 'connected') return;
    if (st === 'armed') {
      this.sendJson({ type: 'disarm_knowledge_capture' });
      this.setCaptureState('idle');
      return;
    }
    if (this.sendJson({ type: 'arm_knowledge_capture' })) {
      this.setCaptureState('armed');
    }
  }

  private setCaptureState(state: KnowledgeCaptureState): void {
    this._knowledgeCaptureState.set(state);
    if (this.armFailsafeTimer) {
      clearTimeout(this.armFailsafeTimer);
      this.armFailsafeTimer = null;
    }
    if (state === 'armed') {
      // Mirror the backend's 60s arm TTL so a lost message can't stick the UI.
      this.armFailsafeTimer = setTimeout(() => {
        if (this._knowledgeCaptureState() === 'armed') this._knowledgeCaptureState.set('idle');
      }, 60_000);
    }
  }

  private flashCaptureError(text: string): void {
    this._knowledgeCaptureError.set(text);
    if (this.captureErrorTimer) clearTimeout(this.captureErrorTimer);
    this.captureErrorTimer = setTimeout(() => this._knowledgeCaptureError.set(null), 6_000);
  }

  // ── Teardown ────────────────────────────────────────────────────────
  async stop(): Promise<void> {
    this.cancelled = true;

    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    if (this.avsyncTimer) {
      clearInterval(this.avsyncTimer);
      this.avsyncTimer = null;
    }
    this.avsyncPrev.clear();
    this.localStream?.getTracks().forEach(t => t.stop());
    this.localStream = null;
    this._selfView.set(null);
    this._cameraOn.set(false);
    this._knowledgeCandidates.set([]);
    this.setCaptureState('idle');
    this._knowledgeCaptureError.set(null);
    if (this.captureErrorTimer) {
      clearTimeout(this.captureErrorTimer);
      this.captureErrorTimer = null;
    }

    try { this.dc?.close(); } catch { /* ignore */ }
    this.dc = null;

    try { this.pc?.close(); } catch { /* ignore */ }
    this.pc = null;

    const pcId = this.pcId;
    this.pcId = null;
    if (pcId) this.deleteSession(pcId);

    this.attachmentUrls.forEach(u => URL.revokeObjectURL(u));
    this.attachmentUrls = [];

    this._status.set('ended');
  }

  // ── HTTP signalling ─────────────────────────────────────────────────
  private async postOffer(payload: {
    sdp: string; type: string; pc_id?: string; lead_id?: string;
  }): Promise<OfferResponse> {
    const res = await fetch(`${API_BASE}/api/avatar_video/offer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json())?.detail ?? detail; } catch { /* ignore */ }
      throw new Error(detail);
    }
    return res.json() as Promise<OfferResponse>;
  }

  private deleteSession(pcId: string): void {
    // pc_id is like "SmallWebRTCConnection#0" — '#' is a URL fragment delimiter
    // and would be stripped by the browser, so encode it ('#' → '%23').
    fetch(`${API_BASE}/api/avatar_video/session/${encodeURIComponent(pcId)}`, {
      method: 'DELETE',
    }).catch(() => undefined);
  }

  private waitForIceGathering(pc: RTCPeerConnection): Promise<void> {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise<void>((resolve) => {
      const done = () => {
        pc.removeEventListener('icegatheringstatechange', check);
        resolve();
      };
      const check = () => {
        if (pc.iceGatheringState === 'complete') done();
      };
      pc.addEventListener('icegatheringstatechange', check);
      // Fallback: some browsers don't reliably reach 'complete'; proceed anyway.
      setTimeout(done, ICE_TIMEOUT_MS);
    });
  }
}
