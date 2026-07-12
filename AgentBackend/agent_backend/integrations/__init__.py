"""Outbound integrations from the conversation engine to sibling services.

Currently just the BusinessLayer (lead orchestration / analysis / memory). All
integrations here are ADDITIVE and best-effort: with the relevant config unset
they are no-ops, and on any failure they log and return without affecting the
live call / chat.
"""
