"""Director-briefing tool group — tools for the avatar_video analytics presenter.

Gated to the avatar_video channel via `_group_for_channel` (returns "director").
These tools are NEVER offered to the counsellor channels (voice/whatsapp/chat)
and vice-versa, keeping the two personas' capabilities cleanly separated.

Phase 1: empty (avatar speaks as the director persona, no tools yet).
Phase 3 adds `present_analytics.py` — the generative-UI tool that fetches
outreach stats and emits a validated UiDirective to the browser.
"""
