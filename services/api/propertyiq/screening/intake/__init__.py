"""Tier 2 document intake: rent rolls and T-12 operating statements.

Flow: upload -> extract (code first) -> review and correct -> approve -> normalize -> anomaly checks.
AI is used only to suggest a category for a T-12 label the synonym list doesn't recognize, and
only after tenant details are removed. All arithmetic and every flag decision is plain, tested code.
"""
