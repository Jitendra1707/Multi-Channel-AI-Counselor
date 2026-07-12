import { Injectable, signal } from '@angular/core';
import {
  Room,
  RoomEvent,
  Track,
  type RemoteParticipant,
  type RemoteTrack,
  type RemoteTrackPublication,
} from 'livekit-client';
import { environment } from '../../../environments/environment';

/**
 * MeetingService — control plane (HTTP) + media plane (livekit-client) for the
 * meetings feature. Angular port of the Next.js web-app meeting integration.
 *
 * Control plane → the live-kit meeting service (environment.meetingUrl, :8003):
 *   scheduleMeeting · createInstant · getToken
 * Add the AI agent → AegisBackend (environment.aegisUrl, :8001).
 * Media plane → connect()/disconnect() join the LiveKit SFU (environment.livekitUrl)
 *   with a minted token; reactive state is exposed as signals (OnPush-friendly).
 *
 * Mirrors the conventions of vcons/webrtc-avatar.service.ts: fetch (not
 * HttpClient), signal state, @Injectable() provided at the component so each
 * room gets a fresh instance torn down with the screen.
 */

const MEETING_BASE = (environment.meetingUrl || '').replace(/\/$/, '');
const AEGIS_BASE = (environment.aegisUrl || '').replace(/\/$/, '');
const LIVEKIT_URL = environment.livekitUrl || '';

export type MeetingStatus = 'idle' | 'connecting' | 'connected' | 'ended' | 'error';

export interface ParticipantInvite {
  role: string;
  identity: string;
  display_name: string;
  token: string;
  join_url: string;
}

export interface ScheduleResponse {
  room: string;
  candidate: ParticipantInvite;
  counsellor: ParticipantInvite;
  url: string;
}

export interface TokenResponse {
  room: string;
  identity: string;
  token: string;
  url: string;
}

export interface CreateMeetingResponse {
  room: string;
  /** One public link to share with anyone (tokenless; they enter their name). */
  share_url: string;
  url: string;
}

/** A participant tile rendered in the room (local or remote). */
export interface RoomParticipant {
  identity: string;
  name: string;
  isLocal: boolean;
  isAgent: boolean;
  speaking: boolean;
}

@Injectable()
export class MeetingService {
  // ── reactive state (signals) ──────────────────────────────────────────────
  private readonly _status = signal<MeetingStatus>('idle');
  private readonly _room = signal<string>('');
  private readonly _participants = signal<RoomParticipant[]>([]);
  private readonly _muted = signal(false);
  private readonly _cameraOn = signal(true);
  private readonly _agentAdded = signal(false);
  private readonly _error = signal<string | null>(null);

  // Active session for the EMBEDDED LiveKit Meet (iframe) — {room, token,
  // serverUrl}. Set by enterRoom(); the room component builds the Meet /custom
  // iframe URL from these. Null when not in a room.
  private readonly _session = signal<{ room: string; token: string; serverUrl: string } | null>(null);

  readonly status = this._status.asReadonly();
  readonly roomName = this._room.asReadonly();
  readonly participants = this._participants.asReadonly();
  readonly muted = this._muted.asReadonly();
  readonly cameraOn = this._cameraOn.asReadonly();
  readonly agentAdded = this._agentAdded.asReadonly();
  readonly error = this._error.asReadonly();
  readonly session = this._session.asReadonly();

  private lkRoom: Room | null = null;
  /** identity → <video>/<audio> elements, attached by the component. */
  readonly mediaEls = new Map<string, HTMLMediaElement[]>();

  /** Enter a room via the EMBEDDED LiveKit Meet iframe (the polished prebuilt
   *  UI). Stores room+token+serverUrl; the room component renders the iframe.
   *  This is the path used instead of connect() now that media is handled by
   *  the embedded Meet app, not livekit-client. */
  enterRoom(room: string, token: string, serverUrl: string): void {
    this._room.set(room);
    this._agentAdded.set(false);
    this._session.set({ room, token, serverUrl });
    this._status.set('connected');
  }

  /** Public link to share for a room (the app's /meeting/<room> page). */
  shareLinkFor(room: string): string {
    const base = (typeof window !== 'undefined' ? window.location.origin : '').replace(/\/$/, '');
    return `${base}/meeting/${encodeURIComponent(room)}`;
  }

  // ── control plane (HTTP) ──────────────────────────────────────────────────

  /** Create a room + mint a candidate & counsellor invite (human↔human). */
  async scheduleMeeting(body: {
    candidate_name: string;
    counsellor_name?: string;
    room?: string;
  }): Promise<ScheduleResponse> {
    return this.postJson<ScheduleResponse>(`${MEETING_BASE}/schedule`, {
      candidate_name: body.candidate_name,
      counsellor_name: body.counsellor_name ?? 'Counsellor',
      room: body.room,
    });
  }

  /** Create ONE meeting room and get a single shareable, tokenless link
   *  (Google-Meet style). No names at creation — each opener enters their own. */
  async createMeeting(): Promise<CreateMeetingResponse> {
    return this.postJson<CreateMeetingResponse>(`${MEETING_BASE}/meeting`, {});
  }

  /** Instant meeting: create a room, then mint a token for THIS user to join. */
  async createInstant(displayName: string, role = 'counsellor'): Promise<TokenResponse> {
    const created = await this.postJson<{ room: string }>(`${MEETING_BASE}/rooms`, {});
    return this.getToken(created.room, displayName, role);
  }

  /** Mint a join token for an existing room (join-with-your-name flow). */
  async getToken(room: string, displayName: string, role = 'guest'): Promise<TokenResponse> {
    return this.postJson<TokenResponse>(`${MEETING_BASE}/token`, {
      room,
      display_name: displayName,
      role,
    });
  }

  /** Dispatch the AI agent into a room (AegisBackend). `panel` = silent co-pilot
   *  that answers when addressed; `solo` = answers everything. */
  async addAgent(room: string, mode: 'panel' | 'solo' = 'panel'): Promise<void> {
    await this.postJson<unknown>(`${AEGIS_BASE}/api/meeting/agent/join`, { room, mode });
    this._agentAdded.set(true);
  }

  // ── media plane (livekit-client) ──────────────────────────────────────────

  /** Join the SFU with a minted token and start publishing mic (+ camera). */
  async connect(room: string, token: string, opts?: { video?: boolean }): Promise<void> {
    if (this.lkRoom) return; // already connected
    if (!LIVEKIT_URL) {
      this._status.set('error');
      this._error.set('LiveKit URL is not configured (environment.livekitUrl).');
      return;
    }
    this._status.set('connecting');
    this._error.set(null);
    this._room.set(room);

    // Force browser echo-cancellation/noise-suppression/auto-gain on the
    // published mic so this participant's track does NOT carry the other
    // participants' playback (the multi-party duplicate-transcription cause:
    // A's voice plays from B's speakers → B's mic recaptures it → A is
    // transcribed twice). Plain booleans only request the DEFAULT canceller,
    // which leaks on speakers; the Chrome aggressive AEC hints engage the
    // strongest available. Mirrors AEC_CONSTRAINTS in livekit-room.react.tsx —
    // keep both in sync. Non-standard keys cast through `any`.
    const lk = new Room({
      adaptiveStream: true,
      dynacast: true,
      audioCaptureDefaults: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        echoCancellationType: 'system',
        googEchoCancellation: true,
        googAutoGainControl: true,
        googNoiseSuppression: true,
        googHighpassFilter: true,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any,
    });
    this.lkRoom = lk;

    lk.on(RoomEvent.ParticipantConnected, () => this.syncParticipants());
    lk.on(RoomEvent.ParticipantDisconnected, () => this.syncParticipants());
    lk.on(RoomEvent.ActiveSpeakersChanged, () => this.syncParticipants());
    lk.on(RoomEvent.TrackSubscribed, (track: RemoteTrack, _pub: RemoteTrackPublication, p: RemoteParticipant) =>
      this.onRemoteTrack(track, p),
    );
    lk.on(RoomEvent.Disconnected, () => {
      this._status.set('ended');
      this.syncParticipants();
    });

    try {
      await lk.connect(LIVEKIT_URL, token);
      await lk.localParticipant.setMicrophoneEnabled(true);
      if (opts?.video !== false) {
        await lk.localParticipant.setCameraEnabled(true);
        this._cameraOn.set(true);
      } else {
        this._cameraOn.set(false);
      }
      this._status.set('connected');
      this.syncParticipants();
    } catch (e) {
      this._status.set('error');
      this._error.set(e instanceof Error ? e.message : 'Failed to join the meeting');
      this.lkRoom = null;
    }
  }

  async toggleMute(): Promise<void> {
    if (!this.lkRoom) return;
    const next = !this._muted();
    await this.lkRoom.localParticipant.setMicrophoneEnabled(!next);
    this._muted.set(next);
  }

  async toggleCamera(): Promise<void> {
    if (!this.lkRoom) return;
    const next = !this._cameraOn();
    await this.lkRoom.localParticipant.setCameraEnabled(next);
    this._cameraOn.set(next);
  }

  async leave(): Promise<void> {
    if (this.lkRoom) {
      await this.lkRoom.disconnect();
      this.lkRoom = null;
    }
    this.mediaEls.clear();
    this._session.set(null);     // tears down the embedded Meet iframe
    this._status.set('ended');
    this._participants.set([]);
  }

  /** Attach a participant's video track to a host element (called by the view). */
  attachVideo(identity: string, el: HTMLVideoElement): void {
    const room = this.lkRoom;
    if (!room) return;
    const all = [room.localParticipant, ...room.remoteParticipants.values()];
    const p = all.find((x) => x.identity === identity);
    const pub = p ? [...p.trackPublications.values()].find((tp) => tp.kind === Track.Kind.Video) : undefined;
    if (pub?.track) pub.track.attach(el);
  }

  // ── internals ─────────────────────────────────────────────────────────────
  private onRemoteTrack(track: RemoteTrack, _p: RemoteParticipant): void {
    // Audio must be attached to play (esp. the agent's TTS). Video is attached
    // by the view via attachVideo() once its tile element exists.
    if (track.kind === Track.Kind.Audio) {
      const el = track.attach();
      el.autoplay = true;
      document.body.appendChild(el); // hidden; audio only
    }
    this.syncParticipants();
  }

  private syncParticipants(): void {
    const room = this.lkRoom;
    if (!room) {
      this._participants.set([]);
      return;
    }
    const active = new Set(room.activeSpeakers.map((s) => s.identity));
    const toTile = (p: { identity: string; name?: string; metadata?: string }, isLocal: boolean): RoomParticipant => {
      const meta = (p.metadata || '').toLowerCase();
      return {
        identity: p.identity,
        name: p.name || p.identity,
        isLocal,
        isAgent: meta === 'agent' || p.identity.includes('agent'),
        speaking: active.has(p.identity),
      };
    };
    const tiles: RoomParticipant[] = [
      toTile(room.localParticipant, true),
      ...[...room.remoteParticipants.values()].map((p) => toTile(p, false)),
    ];
    this._participants.set(tiles);
  }

  private async postJson<T>(url: string, body: unknown): Promise<T> {
    let res: Response;
    try {
      res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch {
      throw new Error('SERVICE_UNAVAILABLE');
    }
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        detail = (await res.json())?.detail ?? detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return res.json() as Promise<T>;
  }
}
