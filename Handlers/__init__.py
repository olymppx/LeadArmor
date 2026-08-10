from __future__ import annotations

from .admin_panel import router as admin_panel_router
from .manager_alerts import router as manager_alerts_router
from .onboarding import router as onboarding_router

routers = [onboarding_router, manager_alerts_router, admin_panel_router]
