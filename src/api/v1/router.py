from fastapi import APIRouter
from .action_items import action_items_router
from .feed import feed_router
from .search import search_router
from .info import info_router
from .health import health_router
from .status import status_router
from .feedback import feedback_router
from .profile import profile_router
from .alerts import alerts_router
from .digest import digest_router
from .similar import similar_router
from .meta import meta_router
from .trends import trends_router
from .landscape import landscape_router
from .context_pack import context_pack_router
from .embed_render import embed_router
from .signals import signals_router
from .sla import sla_router
from .diff import diff_router
from .watchlists import watchlists_router
from .threads import threads_router
from .admin import admin_router
from .auth import auth_router
from .library import library_router

v1_router = APIRouter(prefix="/v1", tags=["v1"])

v1_router.include_router(action_items_router)
v1_router.include_router(feed_router)
v1_router.include_router(search_router)
v1_router.include_router(info_router)
v1_router.include_router(health_router)
v1_router.include_router(status_router)
v1_router.include_router(feedback_router)
v1_router.include_router(profile_router)
v1_router.include_router(alerts_router)
v1_router.include_router(digest_router)
v1_router.include_router(similar_router)
v1_router.include_router(meta_router)
v1_router.include_router(trends_router)
v1_router.include_router(landscape_router)
v1_router.include_router(context_pack_router)
v1_router.include_router(embed_router)
v1_router.include_router(signals_router)
v1_router.include_router(sla_router)
v1_router.include_router(diff_router)
v1_router.include_router(watchlists_router)
v1_router.include_router(threads_router)
v1_router.include_router(admin_router)
v1_router.include_router(auth_router)
v1_router.include_router(library_router)
