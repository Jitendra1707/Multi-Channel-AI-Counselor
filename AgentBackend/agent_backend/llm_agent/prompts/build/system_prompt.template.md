<!-- system_prompt.template.md — the canonical assembled SHAPE of the counsellor
     system prompt. This documents the order prompts/loader.py + system.py
     produce; it is not read at runtime (the loader composes from the modules
     directly). {{slots}} are filled per turn by agent.py. -->

{{behavior_block(profile)}}        <!-- system/: identity, mission, objectives, constraints, state_machine, tool_rules, grounding, safety, escalation (subset per profile) -->

{{style_module(channel)}}          <!-- styles/: voice_style | output_style | avatar_style -->

{{examples_block}}                 <!-- full profile only -->
{{anti_patterns_block}}            <!-- full profile only -->

PERSONA …                          <!-- identity JSON: name, role, voice, objectives, cta_menu -->
[VISUAL CONTEXT …]                 <!-- vision channels, gated -->
KNOWLEDGE (core) …                 <!-- RAG core_context -->
LEAD PROFILE …                     <!-- + OPENING HINT (status_playbook) -->
CONVERSATION STATE …               <!-- when enabled + present -->
[KNOWLEDGE (retrieved) …]          <!-- on-demand RAG -->
YOU ARE SPEAKING WITH …
GENDER …                           <!-- LAST: highest recency -->
