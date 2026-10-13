"""Roles and permissions (Phase 0.5).

What a user can SEE is set by their fund grants (user_portfolio_access). What they can DO is set by
their role. The two are independent: an analyst can be granted one fund or both.

| Permission  | viewer | analyst | credit_officer | admin | Covers                                                  |
|-------------|--------|---------|----------------|-------|---------------------------------------------------------|
| view        | yes    | yes     | yes            | yes   | dashboards, chat, debt, deals, flags, exports           |
| work_deals  |        | yes     | yes            | yes   | create and edit deals, upload and review documents,     |
|             |        |         |                |       | approve extracted figures, market and California checks,|
|             |        |         |                |       | loan terms, propose adjustments                         |
| decide      |        |         | yes            | yes   | approve, reject or undo adjustments; dismiss or reopen  |
|             |        |         |                |       | flags; change or delete a decided adjustment            |
| policy      |        |         | yes            | yes   | lender guideline sets, benchmarks, California rules,    |
|             |        |         |                |       | useful-life table, screening settings                   |
| admin       |        |         |                | yes   | security checklist, lockdown, access audit              |

Every refusal is written to the access audit (propertyiq/audit.py).
"""
from __future__ import annotations

from fastapi import HTTPException

from propertyiq import audit

ROLES = ("viewer", "analyst", "credit_officer", "admin")
PERMISSIONS: dict[str, frozenset[str]] = {
    "view": frozenset(ROLES),
    "work_deals": frozenset({"analyst", "credit_officer", "admin"}),
    "decide": frozenset({"credit_officer", "admin"}),
    "policy": frozenset({"credit_officer", "admin"}),
    "admin": frozenset({"admin"}),
}
ROLE_LABEL = {"viewer": "viewer", "analyst": "analyst", "credit_officer": "credit officer", "admin": "admin"}
# What the refusal says each permission is for (completes "Your role (viewer) can't ...").
PERMISSION_TEXT = {
    "work_deals": "change deals",
    "decide": "approve or reject adjustments or clear flags; that needs a credit officer",
    "policy": "change lender guidelines or screening settings; that needs a credit officer",
    "admin": "use the admin tools",
}


def can(user, permission: str) -> bool:
    return user.role in PERMISSIONS[permission]


def permissions(user) -> list[str]:
    return [p for p, roles in PERMISSIONS.items() if user.role in roles]


def require(user, permission: str, action: str, target_type: str | None = None, target_id=None,
            fund_id: str | None = None) -> None:
    """Raise 403 (and audit the refusal) unless the user's role has this permission."""
    if can(user, permission):
        return
    audit.refused(user, action, "role", target_type, target_id, fund_id)
    raise HTTPException(403, f"Your role ({ROLE_LABEL.get(user.role, user.role)}) can't "
                             f"{PERMISSION_TEXT.get(permission, permission)}.")


def require_fund(user, fund_id: str | None, action: str, target_type: str, target_id, not_found: str) -> None:
    """404 (and audit) when the record belongs to a fund the user isn't granted.

    The response is the same as for a record that doesn't exist, so a user can't tell which IDs exist in
    funds they can't see. fund_id None means the record really doesn't exist (not audited)."""
    if fund_id is None:
        raise HTTPException(404, not_found)
    if fund_id not in user.portfolios:
        audit.refused(user, action, "fund", target_type, target_id, fund_id)
        raise HTTPException(404, not_found)
