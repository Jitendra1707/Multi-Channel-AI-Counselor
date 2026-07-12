/** App environment (production). Set these to the deployed PUBLIC origins.
 *  meetingUrl = the live-kit meeting service public URL; livekitUrl = the public
 *  wss:// SFU the browser connects to (the same self-hosted LiveKit). */
export const environment = {
  production: true,
  // A/V-sync diagnostics (see environment.ts). Left ON so we can capture real
  // production bad-calls while diagnosing the intermittent lip-sync lag; flip
  // false once done. Observe-only — no behavior change, just console logging.
  avatarAvsyncDebug: true,
  // Receiver-side jitter-buffer target (ms) for BOTH avatar audio+video tracks
  // (keeps them in sync at a bounded depth). See environment.ts for tuning notes.
  avatarJitterBufferMs: 200,
  aegisUrl: '',
  businessUrl: '',
  meetingUrl: '',
  livekitUrl: '',
};
