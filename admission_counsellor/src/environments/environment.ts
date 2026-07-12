/**
 * App environment (development default).
 *
 * `aegisUrl` is the AegisBackend base URL the V-Cons avatar call signals against
 * (POST /api/avatar_video/offer, DELETE /api/avatar_video/session/{pc_id}).
 * Overridden for production builds via angular.json `fileReplacements`.
 */
export const environment = {
  production: false,
  // A/V-sync diagnostics: when true, the avatar service logs a ~1Hz [avatar-avsync]
  // line from pc.getStats() (per-track jitter-buffer depth, loss, fps, RTT) so we can
  // attribute intermittent lip-sync lag to the frontend buffer / GPU server / network.
  // Observe-only (no behavior change). Flip false once the diagnosis is done.
  avatarAvsyncDebug: true,
  // Receiver-side jitter-buffer target (ms) applied to BOTH avatar audio and video
  // tracks so the browser keeps them in sync at a bounded depth. ~200ms absorbs
  // network jitter; raise if the avatar stutters on poor networks, lower for snappier
  // (riskier) playout.
  avatarJitterBufferMs: 200,
  // LOCAL — all services on localhost; self-hosted LiveKit on :7880.
  aegisUrl: 'http://localhost:8001',
  businessUrl: 'http://localhost:8002',
  // Meeting control plane (the live-kit/ service).
  meetingUrl: 'http://localhost:8003',
  // LiveKit SFU the browser connects to for media — self-hosted open-source.
  livekitUrl: 'ws://localhost:7880',
  // --- PUBLIC DEMO rollback (dev tunnels + Cloud) ---
  // aegisUrl: 'https://2xs73dg9-8001.inc1.devtunnels.ms',
  // meetingUrl: 'https://2xs73dg9-8003.inc1.devtunnels.ms',
  // livekitUrl: 'wss://aegis-vhxcekzo.livekit.cloud',
};
