"""Admin API. Every route except the lockdown banner status needs the 'admin' role."""
from typing import Callable, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from propertyiq import access, audit, db
from propertyiq.admin import checklist, lockdown


class LockdownRequest(BaseModel):
    mode: Literal["app", "computer", "off"]


def build_router(user_dependency: Callable) -> APIRouter:
    router = APIRouter()

    def admin_user(request: Request, user=Depends(user_dependency)):
        # Refusals are audited with the route as the action, e.g. admin.checklist.
        access.require(user, "admin", "admin." + request.url.path.rsplit("/", 1)[-1].replace("-", "_"), "admin")
        return user

    @router.get("/api/lockdown/status")
    def lockdown_banner(_=Depends(user_dependency)) -> dict:
        """For the header banner; any signed-in user. Reads a local state file, so it's instant."""
        return lockdown.quick_mode()

    @router.get("/api/admin/checklist")
    def security_checklist(request: Request, user=Depends(admin_user)) -> dict:
        lockdown.log_action(user.username, "viewed security checklist")
        return checklist.build(request.app)

    @router.get("/api/admin/lockdown")
    def lockdown_status(_=Depends(admin_user)) -> dict:
        return {**lockdown.firewall_status(), "api_can_reach_internet": lockdown.can_reach_internet()}

    @router.post("/api/admin/lockdown", status_code=202)
    def change_lockdown(body: LockdownRequest, user=Depends(admin_user)) -> dict:
        lockdown.request_change(body.mode, user.username)
        audit.allowed(user, "admin.lockdown", "lockdown", body.mode)
        return {"requested": body.mode,
                "message": "Windows is asking for permission on this PC's screen. Click Yes there to apply it."}

    @router.get("/api/admin/audit")
    def access_audit(_=Depends(admin_user), outcome: Literal["allowed", "refused"] | None = None,
                     limit: int = Query(200, ge=1, le=1000)) -> dict:
        with db.connection() as conn:
            return audit.recent(conn, limit, outcome)

    return router
