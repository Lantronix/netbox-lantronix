from django.urls import path
from netbox.api.routers import NetBoxRouter

from . import views

app_name = "netbox_lantronix-api"

router = NetBoxRouter()
router.register("sources", views.PercepxionSourceViewSet)
router.register("links", views.DeviceLinkViewSet)
router.register("rules", views.ModelRuleViewSet)
router.register("overrides", views.DeviceTypeOverrideViewSet)

urlpatterns = [
    path(
        "sources/<int:pk>/events/",
        views.SourceEventsView.as_view(),
        name="percepxionsource-events",
    ),
    *router.urls,
]
