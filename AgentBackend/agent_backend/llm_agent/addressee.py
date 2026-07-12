"""Addressee gate — decide if a transcript is meant for the bot.

Voice meetings are noisy. Two humans talking to each other should not
trigger the bot. The gate lets a turn through ONLY when one of these
holds:

  1. The user said the bot's name (or a close-enough STT mishearing)
     somewhere in the utterance.
  2. The utterance starts with an imperative verb the bot understands
     ("leave", "share", "schedule", "show", ...) — covers commands
     said without the wake-word.
  3. The user is asking politely ("can you ...", "could you ...",
     "please ...") followed by an imperative verb.

`VOICE_REQUIRE_ADDRESS=false` in `.env` disables the gate (1:1 demo
mode — every transcript reaches the brain).

Fuzzy match
-----------
STT often mangles short names (Aisha → Ayesha, Iyesha, Isha, Ayisha).
The gate accepts any whole-word token within a small edit-distance of
the canonical name OR any persona-JSON `aliases` entry, with the
threshold scaled to candidate length:

  - ≤ 4 chars: edit distance ≤ 1
  - longer:   edit distance ≤ 2

A length-difference guard prevents long unrelated words from
matching short candidates (e.g. "alphabet" against "aisha").

Mute / unmute
-------------
The gate also recognises explicit verbal mute commands ("be quiet",
"stop talking", "mute yourself") and the inverse ("you can talk
again", "unmute"). Callers decide what to do — typically a stateful
processor toggles a flag and emits an acknowledgement.

This module is pure logic with no Pipecat dependency, so it can be
unit-tested in isolation and reused from non-voice channels (Phase 2)
that want the same gating behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Imperative verbs the bot supports. If the user starts with one of
# these, we treat the turn as a command even without a wake-word —
# matches how humans naturally bark orders mid-meeting ("share the
# slide", "leave the meeting"). Curated to the verbs the bot can
# actually act on; expand when new tools land (Phase 4).
_IMPERATIVE_VERBS: frozenset[str] = frozenset(
    {
        "leave", "share", "show", "schedule", "send", "give", "run", "stop",
        "hide", "open", "navigate", "look", "smile", "join", "speak", "say",
        "tell", "explain", "summarize", "summarise", "remind", "find",
        "search", "draft", "write", "play", "pause", "resume", "cancel",
        "mute", "unmute",
    }
)

# Polite prefixes that pair with a verb at position 3 ("can you share
# the slide?", "could you please summarise?").
_POLITE_PREFIXES: frozenset[str] = frozenset({"can", "could", "would", "please"})

_MUTE_PATTERNS: tuple[str, ...] = (
    "mute yourself", "be quiet", "stay quiet", "hush",
    "stop talking", "shut up", "don't speak", "do not speak",
    "stop speaking", "go quiet", "be silent",
)

_UNMUTE_PATTERNS: tuple[str, ...] = (
    "you can speak", "you can talk", "unmute yourself", "unmute",
    "speak again", "you can chat", "feel free to speak",
)


@dataclass(frozen=True)
class GateDecision:
    """Why the gate accepted (or rejected) a turn.

    Attributes:
        allowed: True if the brain should run on this turn.
        addressed: True if the bot's name (or fuzzy variant) was heard.
        is_command: True if the turn looks imperative even without name.
        wants_mute: User explicitly asked the bot to be quiet.
        wants_unmute: User explicitly asked the bot to talk again.
        reason: Short human-readable reason, for logs.
    """

    allowed: bool
    addressed: bool
    is_command: bool
    wants_mute: bool
    wants_unmute: bool
    reason: str


class AddresseeGate:
    """Stateless gate that combines fuzzy-name + command detection.

    State (mute) lives in the caller — keeping this object stateless
    makes it cheap to construct, easy to test, and safe to share
    across pipelines if we ever scale that way.
    """

    def __init__(
        self,
        *,
        bot_name: str = "",
        aliases: list[str] | tuple[str, ...] = (),
        require_address: bool = True,
    ) -> None:
        cleaned = (bot_name or "").strip().lower()
        self._bot_name = cleaned

        # Whole-word regex over the canonical name — short-circuits the
        # fuzzy pass when STT lands an exact match.
        self._bot_name_re = (
            re.compile(rf"\b{re.escape(cleaned)}\b", re.IGNORECASE)
            if cleaned
            else None
        )

        # Normalise aliases: lowercase, strip, drop empties + canonical
        # (already handled separately), preserve first-seen order.
        seen: set[str] = {cleaned} if cleaned else set()
        norm_aliases: list[str] = []
        for raw in aliases or ():
            a = (raw or "").strip().lower()
            if not a or a in seen:
                continue
            seen.add(a)
            norm_aliases.append(a)
        self._aliases: tuple[str, ...] = tuple(norm_aliases)

        # One regex matches any alias as a whole word — cheaper than
        # scanning each one separately, and gives us a fast exact path.
        self._alias_re = (
            re.compile(
                r"\b(?:" + "|".join(re.escape(a) for a in self._aliases) + r")\b",
                re.IGNORECASE,
            )
            if self._aliases
            else None
        )

        # Auto-disable the gate when no name is configured — otherwise
        # the bot would be silent forever. Tested explicitly so an
        # operator can flip the env knob without surprises.
        self._require_address = require_address and bool(cleaned)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def require_address(self) -> bool:
        """Whether the gate is active. False ⇒ every turn is allowed."""
        return self._require_address

    @property
    def bot_name(self) -> str:
        return self._bot_name

    @property
    def aliases(self) -> tuple[str, ...]:
        return self._aliases

    def evaluate(self, text: str, *, muted: bool = False) -> GateDecision:
        """Classify one transcript.

        Args:
            text: The STT output for this user turn.
            muted: Whether the caller is currently in muted state.
                Affects the `allowed` decision: a muted bot only
                un-gates on `addressed` or `wants_unmute`.

        Returns:
            A `GateDecision` the caller can act on. `allowed=True`
            means "run the brain on this turn". `wants_mute` /
            `wants_unmute` are independent signals the caller uses
            to toggle its mute state and emit an ack line.
        """
        lower = (text or "").lower()
        if not lower.strip():
            return GateDecision(
                allowed=False,
                addressed=False,
                is_command=False,
                wants_mute=False,
                wants_unmute=False,
                reason="empty",
            )

        addressed = self._is_addressed(lower)
        wants_mute = _matches_any(lower, _MUTE_PATTERNS)
        wants_unmute = _matches_any(lower, _UNMUTE_PATTERNS)
        is_command = self._looks_commandy(lower)

        # Muted state: only "addressed" or explicit unmute pierces it.
        # Everything else stays silent.
        if muted:
            if addressed or wants_unmute:
                return GateDecision(
                    allowed=True,
                    addressed=addressed,
                    is_command=is_command,
                    wants_mute=False,
                    wants_unmute=wants_unmute,
                    reason="unmuted-by-user" if wants_unmute else "addressed-while-muted",
                )
            return GateDecision(
                allowed=False,
                addressed=False,
                is_command=is_command,
                wants_mute=False,
                wants_unmute=False,
                reason="muted",
            )

        # Not muted. If the user is asking us to mute, allow the turn
        # so the caller can emit the ack ("Okay, I'll stay quiet").
        if wants_mute:
            return GateDecision(
                allowed=True,
                addressed=addressed,
                is_command=is_command,
                wants_mute=True,
                wants_unmute=False,
                reason="mute-request",
            )

        # Gate disabled (require_address=False) → everything goes.
        if not self._require_address:
            return GateDecision(
                allowed=True,
                addressed=addressed,
                is_command=is_command,
                wants_mute=False,
                wants_unmute=wants_unmute,
                reason="gate-disabled",
            )

        # Standard path: addressed OR command-shaped.
        if addressed or is_command:
            return GateDecision(
                allowed=True,
                addressed=addressed,
                is_command=is_command,
                wants_mute=False,
                wants_unmute=wants_unmute,
                reason="addressed" if addressed else "command",
            )

        return GateDecision(
            allowed=False,
            addressed=False,
            is_command=False,
            wants_mute=False,
            wants_unmute=wants_unmute,
            reason="not-addressed",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _is_addressed(self, lower: str) -> bool:
        if not self._bot_name:
            return True  # no name configured ⇒ gate inert

        # Layer 1 — exact whole-word match on canonical name or alias.
        # Cheapest path; matches `aisha`, `ayesha`, etc. as whole words.
        if self._bot_name_re and self._bot_name_re.search(lower):
            return True
        if self._alias_re and self._alias_re.search(lower):
            return True

        # Layer 2 — prefix-extension match. Catches stuttered or
        # elongated STT mishears like "aishaaa", "ayeshahhh",
        # "aishaan", "ayeshay" where the candidate is at the START
        # of a longer token. We require `startswith` (not arbitrary
        # substring) so unrelated words that happen to contain the
        # name mid-string don't trip the gate — "maishan" shouldn't
        # match "aisha" but "aishaaaa" should. Candidate must be ≥4
        # chars to avoid short-alias collisions.
        candidates: tuple[str, ...] = (self._bot_name,) + self._aliases
        tokens = re.findall(r"[a-z']+", lower)
        for cand in candidates:
            if len(cand) < 4:
                continue
            for token in tokens:
                if len(token) > len(cand) and token.startswith(cand):
                    return True

        # Layer 3 — single-token fuzzy match. Per-candidate edit-
        # distance budget:
        #   - candidates ≤ 5 chars: threshold 1
        #   - candidates ≥ 6 chars: threshold 2
        # The 5-char boundary was chosen after the original ≤4
        # boundary let `fish` match `aisha` (2 edits — both at the
        # threshold). Short names need a tighter budget because each
        # edit is a bigger proportional change; the alias list +
        # Layer 2 (prefix) + Layer 4 (joins) cover the common STT
        # variants without needing a generous Layer 3 budget.
        for cand in candidates:
            cand_len = len(cand)
            cand_threshold = 1 if cand_len <= 5 else 2
            for token in tokens:
                if abs(len(token) - cand_len) > 2:
                    continue
                if _edit_distance(token, cand) <= cand_threshold:
                    return True

        # Layer 4 — adjacent two-token join + fuzzy. STT occasionally
        # splits a name across word boundaries — "ay sha", "I sha",
        # "ay-shaa" land as multiple tokens. Join each adjacent pair
        # (no separator) and fuzzy-match the result against every
        # candidate. Same per-candidate threshold as Layer 3.
        if len(tokens) >= 2:
            joins = [tokens[i] + tokens[i + 1] for i in range(len(tokens) - 1)]
            for cand in candidates:
                cand_len = len(cand)
                cand_threshold = 1 if cand_len <= 5 else 2
                for joined in joins:
                    if abs(len(joined) - cand_len) > 2:
                        continue
                    if _edit_distance(joined, cand) <= cand_threshold:
                        return True

        return False

    def _looks_commandy(self, lower: str) -> bool:
        words = lower.split()
        if not words:
            return False
        # Bare imperative — "share the slide".
        if words[0].strip(",.!?") in _IMPERATIVE_VERBS:
            return True
        # Polite prefix → scan the next few words for an imperative
        # verb. Covers natural phrasings:
        #   - "can you share ..."           (verb at 2)
        #   - "could you please share ..."  (verb at 3)
        #   - "would you kindly explain ..."(verb at 3)
        # We bound the scan to the first 5 words to avoid catching
        # the word "share" deep in a long polite digression.
        if words[0] in _POLITE_PREFIXES:
            for w in words[1:5]:
                if w.strip(",.!?") in _IMPERATIVE_VERBS:
                    return True
        return False


# ---------------------------------------------------------------------------
# Module-level helpers (also useful in isolation, e.g. from chat channel)
# ---------------------------------------------------------------------------


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    """Return True if any literal pattern appears in `text`. Used for
    mute / unmute phrase detection — patterns are short phrases, so
    substring match is the right test (not whole-word)."""
    return any(p in text for p in patterns)


def _edit_distance(a: str, b: str) -> int:
    """Iterative Levenshtein distance.

    Both inputs are short (≤ 16 chars in practice — names + STT
    tokens), so the O(len(a) * len(b)) cost is negligible. Inlined
    rather than depending on `rapidfuzz` / `python-Levenshtein` to
    keep the install surface tight.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(
                    cur[-1] + 1,                  # insert
                    prev[j] + 1,                  # delete
                    prev[j - 1] + (ca != cb),     # substitute
                )
            )
        prev = cur
    return prev[-1]


def extract_name_and_aliases(identity: dict) -> tuple[str, list[str]]:
    """Pull the canonical name + alias list out of the identity JSON.

    The persona schema (see `identity/loader.py`) carries:
      - `name`: the canonical display name
      - `aliases`: a list of STT mishears curated per persona

    Empty / missing fields fall back to safe defaults so the caller
    can build a gate without doing its own validation.
    """
    name = str(identity.get("name") or "").strip()
    raw_aliases = identity.get("aliases") or []
    if not isinstance(raw_aliases, list):
        raw_aliases = []
    aliases = [str(a).strip() for a in raw_aliases if a]
    return name, aliases
