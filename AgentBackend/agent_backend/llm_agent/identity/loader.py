"""Identity JSON loader + persona-block renderer.

Loads `<identity_name>.json` from this directory through an
lru_cache. The directory is populated either by a manual drop (dev)
or by the boot-time Azure blob fetcher (prod).

`render_identity_block(identity_dict)` turns the JSON into the
WHO YOU ARE section of the system prompt. The schema is deliberately
permissive — missing fields are skipped so the same renderer works
for every persona (counselor, avatar, future agents) without
per-persona branching.

Standard identity schema v1 (all fields optional except name/id; the
renderer reads both the v1 keys and the older flat keys so existing
personas keep rendering unchanged):

  Identity / header
    - name, pronouns, id
    - role.{title, team, organization|company, reports_to|manager}
  Purpose
    - mission                         — one-line purpose
    - objectives[]                    — what the agent steers toward
    - cta_menu[]                      — concrete next-step options
  Language
    - language.{default, supported[], mirror_user}
  Voice & personality
    - expertise[]                     — legacy avatar field, still rendered
    - voice.{tone, formality, example_phrases|examplePhrases[]}
    - personality.{style, traits[], avoid[]}
    - greeting.{first_turn, returning}
  Guardrails & grounding
    - rules[]                         — soft, topic-scoped guidance
    - guardrails.{never[], always[], disclosures[]}
    - knowledge.{grounding, domains[]} — RAG contract (facts come from context)
    - escalation.{to_human_when[], handoff_message}

Domain FACTS (university programmes, fees, deadlines) never live here —
they arrive at prompt-assembly time from retrieval (RAG).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent_backend.infra import get_logger

log = get_logger(__name__)

_BUNDLE_DIR = Path(__file__).resolve().parent
# Persona file used if the requested IDENTITY_NAME isn't on disk.
# Tweak by editing this file's contents, not the constant.
_DEFAULT_NAME = "aisha-aegis"


@lru_cache(maxsize=8)
def _load_one(name: str) -> dict[str, Any]:
    """Load a specific persona by stem. Cached — survives the
    process until manual `_load_one.cache_clear()` (e.g. on a
    hot identity-refresh in dev)."""
    path = _BUNDLE_DIR / f"{name}.json"
    if not path.exists():
        log.warning(
            "[identity] not found, falling back to default",
            requested=name,
            fallback=_DEFAULT_NAME,
        )
        path = _BUNDLE_DIR / f"{_DEFAULT_NAME}.json"
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"identity file {path} is not a JSON object")
    return data


def get_identity(name: str | None = None) -> dict[str, Any]:
    """Resolve a persona dict by stem.

    Args:
        name: Filename stem (without `.json`). When None, falls back
            to `IDENTITY_NAME` env / `_DEFAULT_NAME`.
    """
    from agent_backend.config import get_settings

    requested = (name or get_settings().identity_name or _DEFAULT_NAME).strip().lower()
    return _load_one(requested)


def list_identities() -> list[str]:
    """Persona names available locally (filename stems). Useful for
    `/health` extensions and dev tooling."""
    return sorted(p.stem for p in _BUNDLE_DIR.glob("*.json"))


def _gender_grammar_line(pronouns: Any) -> str | None:
    """A self-reference gender rule derived from the persona's pronouns, so
    gendered languages (Hindi, Telugu, Marathi, …) use the correct verb/adjective
    forms. Returns None when pronouns are absent/unclear (then no rule is added)."""
    if not isinstance(pronouns, str) or not pronouns.strip():
        return None
    # Token match (NOT substring) — 'they' contains 'he', which would otherwise
    # be misread as masculine.
    tokens = set(pronouns.lower().replace("/", " ").replace(",", " ").split())
    # Short pointer only — the FULL self-reference gender rule (with worked
    # Hindi/Telugu/etc. examples) is rendered once at the very end of the prompt
    # by `gender_reminder` (highest recency). Keeping the detail in one place
    # avoids shipping it twice; this line just flags the constraint up front.
    if tokens & {"she", "her", "hers"}:
        return (
            "  GRAMMATICAL GENDER — you are FEMALE: use feminine first-person "
            "verb/adjective forms about yourself in every gendered language "
            "(full examples in the GENDER REMINDER at the end of this prompt)."
        )
    if tokens & {"he", "him", "his"}:
        return (
            "  GRAMMATICAL GENDER — you are MALE: use masculine first-person "
            "verb/adjective forms about yourself in every gendered language "
            "(full examples in the GENDER REMINDER at the end of this prompt)."
        )
    return (
        "  GRAMMATICAL GENDER — use gender-neutral self-reference wherever the "
        "language allows it."
    )


def gender_reminder(identity: dict[str, Any]) -> str | None:
    """A SHORT, high-recency gender reminder for the very end of the system
    prompt (recency is the strongest lever against mid-sentence gender slips on
    fast models). Derived from the persona's pronouns; None when not gendered."""
    pronouns = identity.get("pronouns") if isinstance(identity, dict) else None
    if not isinstance(pronouns, str) or not pronouns.strip():
        return None
    tokens = set(pronouns.lower().replace("/", " ").replace(",", " ").split())
    if tokens & {"she", "her", "hers"}:
        return (
            "GENDER REMINDER: you are FEMALE — every self-referential verb in "
            "Hindi/Telugu/etc. must be feminine ('samajh gayi', 'karti hoon', "
            "'kar rahi hoon', 'karungi'), NEVER masculine ('gaya', 'karta', "
            "'karunga'). Check this in every sentence."
        )
    if tokens & {"he", "him", "his"}:
        return (
            "GENDER REMINDER: you are MALE — use masculine self-referential verb "
            "forms in gendered languages."
        )
    return None


def render_identity_block(identity: dict[str, Any]) -> str:
    """Render a persona dict as the WHO YOU ARE block of the system
    prompt. Schema-tolerant — any missing field is silently skipped, so
    one renderer serves every persona (see module docstring for the v1
    schema). Reads both v1 keys and the older flat keys so legacy
    personas render unchanged.
    """

    def _slist(value: Any) -> list[str]:
        """Coerce a value to a clean list[str] (drops empties/non-str)."""
        if not isinstance(value, list):
            return []
        return [str(x).strip() for x in value if str(x).strip()]

    lines: list[str] = ["WHO YOU ARE:"]

    # --- Header: name + pronouns ---
    name = identity.get("name") or identity.get("id") or "the assistant"
    pronouns = identity.get("pronouns")
    name_line = f"  You are {name}"
    if isinstance(pronouns, str) and pronouns:
        name_line += f" ({pronouns})"
    lines.append(name_line + ".")

    # --- Grammatical gender (critical for gendered languages) ---
    # Pronouns alone ('she/her') don't stop the model using masculine Hindi/Telugu
    # verb forms ('karta hoon' for a female persona). Spell out the rule so it
    # speaks self-referential verbs/adjectives in the right gender.
    gender_line = _gender_grammar_line(pronouns)
    if gender_line:
        lines.append(gender_line)

    # --- Role (organization is a NAME only; facts come from RAG) ---
    role = identity.get("role")
    if isinstance(role, dict):
        title = role.get("title")
        team = role.get("team")
        org = role.get("organization") or role.get("company")
        if title or team or org:
            bits: list[str] = []
            if title:
                bits.append(str(title))
            if team:
                bits.append(f"on the {team} team")
            if org:
                bits.append(f"at {org}")
            lines.append(f"  Role: {', '.join(bits)}.")

    # --- Mission ---
    mission = identity.get("mission")
    if isinstance(mission, str) and mission.strip():
        lines.append(f"  Mission: {mission.strip()}")

    # --- Expertise (legacy avatar field; kept for back-compat) ---
    expertise = _slist(identity.get("expertise"))
    if expertise:
        lines.append(f"  Expertise: {', '.join(expertise[:5])}.")

    # --- Language ---
    language = identity.get("language")
    if isinstance(language, dict):
        default = language.get("default")
        supported = _slist(language.get("supported"))
        # Don't echo the default inside "you also speak" — it reads as a dupe.
        also = [lang for lang in supported if lang != default]
        if default or also:
            lang_bits = []
            if default:
                lang_bits.append(f"default {default}")
            if also:
                lang_bits.append("you also speak " + ", ".join(also))
            line = "  Languages: " + "; ".join(lang_bits) + "."
            if language.get("mirror_user"):
                line += " Mirror the person's language — if they switch, switch with them."
            lines.append(line)

    # --- Objectives (the agenda — moved out of the hardcoded playbook) ---
    objectives = _slist(identity.get("objectives"))
    if objectives:
        lines.append("  YOUR OBJECTIVES (work toward these; don't leave without trying):")
        for o in objectives[:10]:
            lines.append(f"    - {o}")

    # --- Next-step options (CTA menu) ---
    cta_menu = _slist(identity.get("cta_menu"))
    if cta_menu:
        lines.append("  NEXT-STEP OPTIONS (offer ONE that fits the moment — never list them all):")
        for c in cta_menu[:8]:
            lines.append(f"    - {c}")

    # --- Voice ---
    voice = identity.get("voice")
    if isinstance(voice, dict):
        tone = voice.get("tone")
        if tone:
            lines.append(f"  Voice: {tone}")
        formality = voice.get("formality")
        if formality:
            lines.append(f"  Register: {formality}.")
        # Accept both v1 `example_phrases` and legacy `examplePhrases`.
        examples = _slist(voice.get("example_phrases") or voice.get("examplePhrases"))
        if examples:
            # CAUTION: present examples as STYLE REFERENCE, not as templates
            # to quote verbatim. Earlier wording caused the model to inject
            # these into unrelated replies; the phrasing below tells it these
            # are register/rhythm samples, not stock phrases.
            lines.append(
                "  Voice style reference (DO NOT quote these verbatim — "
                "use only as a guide for register, rhythm, and brevity): "
                + " | ".join(f'"{p}"' for p in examples[:4])
            )

    # --- Personality ---
    personality = identity.get("personality")
    if isinstance(personality, dict):
        style = personality.get("style")
        if style:
            lines.append(f"  Personality: {style}")
        traits = _slist(personality.get("traits"))
        if traits:
            lines.append(f"  Traits: {', '.join(traits)}.")
        avoid = _slist(personality.get("avoid"))
        if avoid:
            lines.append(f"  Avoid: {', '.join(avoid)}.")

    # --- Greeting style ---
    greeting = identity.get("greeting")
    if isinstance(greeting, dict):
        first_turn = greeting.get("first_turn")
        returning = greeting.get("returning")
        if first_turn:
            lines.append(f"  Greeting (first turn): {first_turn}")
        if returning:
            lines.append(f"  Greeting (continuing): {returning}")

    # --- Topic rules — scoped so the LLM doesn't apply them to bot
    # operation or general chat (tight rules can otherwise make it refuse
    # off-topic questions or skip tool calls). ---
    rules = _slist(identity.get("rules"))
    if rules:
        lines.append(
            "  Operating rules (how you conduct the conversation; "
            "do not apply to casual chat):"
        )
        for r in rules[:8]:
            lines.append(f"    - {r}")

    # --- Guardrails (hard limits) ---
    guardrails = identity.get("guardrails")
    if isinstance(guardrails, dict):
        never = _slist(guardrails.get("never"))
        always = _slist(guardrails.get("always"))
        disclosures = _slist(guardrails.get("disclosures"))
        if never:
            lines.append("  NEVER:")
            for n in never[:8]:
                lines.append(f"    - {n}")
        if always:
            lines.append("  ALWAYS:")
            for a in always[:8]:
                lines.append(f"    - {a}")
        if disclosures:
            lines.append("  Disclosures: " + " ".join(disclosures))

    # --- Knowledge grounding (the RAG contract) ---
    knowledge = identity.get("knowledge")
    if isinstance(knowledge, dict):
        grounding = knowledge.get("grounding")
        if grounding:
            lines.append(f"  Grounding: {grounding}")

    # --- Escalation / human handoff ---
    escalation = identity.get("escalation")
    if isinstance(escalation, dict):
        when = _slist(escalation.get("to_human_when"))
        handoff = escalation.get("handoff_message")
        if when:
            lines.append("  Escalate to a human when: " + "; ".join(when) + ".")
        if handoff:
            lines.append(f'  Handoff line: "{handoff}"')

    return "\n".join(lines)
