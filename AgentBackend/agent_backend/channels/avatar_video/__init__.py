"""Avatar video channel — Simli avatar over SmallWebRTC (browser ↔ backend).

Architecture
------------
The browser opens ONE WebRTC peer connection directly to this backend
(aiortc, pure-Python — works on Windows/Linux/macOS). Signalling is a single
HTTP endpoint, so there is no per-session TCP port and no port-collision race.

    1.  Browser: getUserMedia(mic) → RTCPeerConnection → createOffer
          → POST /api/avatar_video/offer {sdp, type, lead_id?}

    2.  Backend: SmallWebRTCConnection.initialize(offer)
          → build pipeline → run it (background) → return SDP answer.

    3.  Pipeline (THE ONE BRAIN, same as every channel):
          SmallWebRTC.input()
            ↓  mic audio
          make_stt()                 (Deepgram | Azure)
            ↓  TranscriptionFrame
          AgentBridge → llm_agent.run_stream(channel="avatar_video")
            ↓  TextFrame              (counselor brain: leads / RAG / playbook)
          make_tts()                 (ElevenLabs | Azure)
            ↓  TTSAudioRawFrame
          SimliVideoService          → Simli lip-syncs to the TTS audio
            ↓  OutputImageRawFrame + TTSAudioRawFrame
          SmallWebRTC.output()       → avatar video+audio streamed to browser

    4.  Browser <video>/<audio> render the avatar in real time; the user
        speaks and the avatar replies — full duplex over one WebRTC peer.

    5.  DELETE /api/avatar_video/session/{pc_id} (or browser tab close)
          → cancels the pipeline + stops Simli.

Channel contract
----------------
session.channel = "avatar_video" is in VOICE_FAMILY → counselor brain
(leads / university / RAG / playbook), identical to WhatsApp and PSTN voice.
"""

from agent_backend.channels.avatar_video.routes import router

__all__ = ["router"]
