/**
 * LiveKitRoomReact — the SAME prebuilt LiveKit room as the Next.js web-app
 * (web-app/src/components/meeting/MeetingRoom.tsx). It renders the real
 * @livekit/components-react <VideoConference> (full grid + control bar:
 * mic / camera / screenshare / leave / settings) + <RoomAudioRenderer>, so the
 * look & feel is identical to web-app — NOT a custom re-creation.
 *
 * Mounted INTO the Angular app via ReactDOM.createRoot (see meeting-room.component.ts).
 * Angular owns routing/state; this React island owns only the room UI.
 *
 * Props are plain values + callbacks so the Angular host stays in control:
 *   serverUrl/token  → connect to the room
 *   roomLabel        → header chip
 *   agentState       → Add-AI button state (driven by Angular)
 *   onAddAi/onLeave  → bubble actions back to Angular
 */
import * as React from 'react';
import {
  LiveKitRoom,
  RoomAudioRenderer,
  VideoConference,
  useRoomContext,
} from '@livekit/components-react';
import { RoomEvent, Track } from 'livekit-client';
import type { LocalAudioTrack, RoomOptions } from 'livekit-client';

export type AgentState = 'idle' | 'adding' | 'added';

/**
 * THE multi-party duplicate-transcription fix (client half).
 *
 * The duplicate is ACOUSTIC ECHO ON THE LISTENER'S DEVICE: when remote person A
 * speaks, A's voice plays out of person B's speakers; B's MIC then re-captures
 * A's voice and publishes a SECOND copy of A's words. The backend runs one STT
 * per track, so A's one sentence is transcribed twice → the agent hears it
 * twice and hallucinates. (This is NOT a same-room problem — it happens between
 * two REMOTE people the moment one of them is on speakers, not headphones.)
 *
 * The cure is to stop the re-capture at the source: the browser's echo
 * canceller must subtract the far-end audio (A's voice) from B's mic BEFORE it
 * is published. That is exactly what Google Meet / Zoom do. Setting
 * `echoCancellation/noiseSuppression/autoGainControl` is necessary but the
 * PLAIN booleans only request the browser default AEC — on speakers that leaks.
 * We additionally pass Chrome's aggressive AEC hints (`echoCancellationType:
 * 'system'` where available, plus the experimental Google constraints) so the
 * STRONGEST available canceller engages. The backend SFU active-speaker gate +
 * transcript dedup are the second and third layers behind this.
 *
 * NOTE: `echoCancellationType` and the `goog*` keys are non-standard
 * (Chrome/Edge only) — cast through `any` so the TS DOM lib doesn't reject
 * them. Browsers that don't understand a constraint ignore it (we don't use
 * `{ exact: … }`, so it never fails the getUserMedia call).
 */
const AEC_CONSTRAINTS = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  // Chrome/Edge: prefer the OS/hardware echo canceller when present, else the
  // aggressive WebRTC AEC3 — both far better at killing speaker recapture than
  // the default. Ignored by browsers that don't support it.
  echoCancellationType: 'system',
  // Legacy experimental Google constraints — still honoured by Chromium and
  // belt-and-braces for older builds.
  googEchoCancellation: true,
  googAutoGainControl: true,
  googNoiseSuppression: true,
  googHighpassFilter: true,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

const ROOM_OPTIONS: RoomOptions = {
  adaptiveStream: true,
  dynacast: true,
  audioCaptureDefaults: AEC_CONSTRAINTS,
};

/**
 * MicAecEnforcer — GUARANTEE the published mic carries echo-cancellation.
 *
 * `RoomOptions.audioCaptureDefaults` only applies when LiveKit itself creates
 * the track; a track acquired another way (device switch, a browser that
 * silently dropped a non-standard constraint, a re-publish) can slip through
 * WITHOUT AEC — and then that participant's mic re-broadcasts everyone else's
 * voice, which is the duplicate-transcription bug. This effect runs inside the
 * room context and, whenever the local mic (re)publishes, calls
 * `restartTrack(AEC_CONSTRAINTS)` to re-acquire it with the aggressive AEC
 * constraints. Idempotent and cheap; a no-op when the track already matches.
 * Renders nothing.
 */
function MicAecEnforcer(): null {
  const room = useRoomContext();

  React.useEffect(() => {
    if (!room) return;

    const enforce = async () => {
      const pub = room.localParticipant.getTrackPublication(Track.Source.Microphone);
      const track = pub?.track as LocalAudioTrack | undefined;
      if (!track) return;
      try {
        // Re-acquire the mic with the aggressive AEC constraints. If the track
        // was already captured with them this is effectively a no-op.
        await track.restartTrack(AEC_CONSTRAINTS);
        // eslint-disable-next-line no-console
        console.info('[meeting] mic AEC constraints enforced', track.mediaStreamTrack.getSettings());
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn('[meeting] could not enforce mic AEC constraints', e);
      }
    };

    const onPublished = () => void enforce();
    room.on(RoomEvent.LocalTrackPublished, onPublished);
    // Also run once now in case the mic was already published before this
    // effect mounted (the common case — VideoConference auto-publishes).
    void enforce();

    return () => {
      room.off(RoomEvent.LocalTrackPublished, onPublished);
    };
  }, [room]);

  return null;
}

export interface LiveKitRoomProps {
  serverUrl: string;
  token: string;
  roomLabel: string;
  video?: boolean;
  agentState: AgentState;
  onAddAi: () => void;
  onLeave: () => void;
}

export function LiveKitRoomReact(props: LiveKitRoomProps): React.ReactElement {
  const { serverUrl, token, roomLabel, video = true, agentState, onAddAi, onLeave } = props;

  // The public, shareable link to THIS meeting (Google-Meet "joining info").
  const shareLink = React.useMemo(() => {
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    return `${origin}/meeting/${encodeURIComponent(roomLabel)}`;
  }, [roomLabel]);

  // Show the share panel automatically when the room opens (like Meet), and
  // let the user reopen it with the Share button.
  const [showShare, setShowShare] = React.useState(true);
  const [copied, setCopied] = React.useState(false);

  const copy = () => {
    void navigator.clipboard?.writeText(shareLink);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  const headerBtn: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    border: '1px solid rgba(99,102,241,.5)', background: 'rgba(99,102,241,.18)',
    color: '#c7ccf5', fontSize: 12, fontWeight: 600, padding: '6px 12px',
    borderRadius: 8, cursor: 'pointer',
  };

  return (
    <div style={{ position: 'relative', display: 'flex', height: '100%', flexDirection: 'column', background: '#0b0b0f' }}>
      <header
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          gap: 12, padding: '8px 14px', borderBottom: '1px solid #1f2430', color: '#e6e8ee',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>Counselling Meeting</span>
          <span style={{ background: '#1b2030', color: '#9aa3b2', fontSize: 11, padding: '2px 8px', borderRadius: 6 }}>
            {roomLabel}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button type="button" onClick={() => setShowShare((s) => !s)} style={headerBtn}>
            🔗 Share
          </button>
          <button
            type="button"
            onClick={onAddAi}
            disabled={agentState !== 'idle'}
            style={{ ...headerBtn, cursor: agentState === 'idle' ? 'pointer' : 'default', opacity: agentState === 'idle' ? 1 : 0.7 }}
          >
            {agentState === 'adding' ? 'Adding AI…' : agentState === 'added' ? '✓ AI joined' : '✨ Add AI'}
          </button>
        </div>
      </header>

      {/* Google-Meet-style share panel: the joining info + copy. */}
      {showShare && (
        <div
          style={{
            position: 'absolute', top: 52, right: 14, zIndex: 20, width: 340,
            background: '#11151f', border: '1px solid #2a3140', borderRadius: 12,
            boxShadow: '0 10px 30px rgba(0,0,0,.5)', padding: 16, color: '#e6e8ee',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 700 }}>Share this meeting</span>
            <button type="button" onClick={() => setShowShare(false)} style={{ background: 'none', border: 0, color: '#9aa3b2', cursor: 'pointer', fontSize: 16 }}>×</button>
          </div>
          <p style={{ fontSize: 12, color: '#9aa3b2', margin: '0 0 10px' }}>
            Anyone with the link can join — they’ll enter their name.
          </p>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              readOnly
              value={shareLink}
              onFocus={(e) => e.currentTarget.select()}
              style={{ flex: 1, background: '#0b0b0f', border: '1px solid #2a3140', borderRadius: 8, color: '#e6e8ee', padding: '8px 10px', fontSize: 12 }}
            />
            <button
              type="button"
              onClick={copy}
              style={{ background: copied ? '#1f9d57' : '#6366f1', border: 0, color: '#fff', fontWeight: 600, fontSize: 12, padding: '8px 12px', borderRadius: 8, cursor: 'pointer', whiteSpace: 'nowrap' }}
            >
              {copied ? '✓ Copied' : 'Copy link'}
            </button>
          </div>
        </div>
      )}

      <div style={{ minHeight: 0, flex: 1 }}>
        <LiveKitRoom
          serverUrl={serverUrl}
          token={token}
          connect
          audio
          video={video}
          options={ROOM_OPTIONS}
          data-lk-theme="default"
          style={{ height: '100%' }}
          onDisconnected={onLeave}
        >
          {/* Full conference grid + control bar (mic/camera/screenshare/leave). */}
          <VideoConference />
          {/* Guarantees the published mic has echo-cancellation (stops this
              device re-broadcasting other people's voices → no dup transcripts). */}
          <MicAecEnforcer />
          {/* Plays every remote audio track — incl. the agent's TTS. */}
          <RoomAudioRenderer />
        </LiveKitRoom>
      </div>
    </div>
  );
}
