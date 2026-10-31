from netbox.api.routers import NetBoxRouter

from . import views

app_name = "netbox_lantronix-api"

router = NetBoxRouter()
router.register("sources", views.PercepxionSourceViewSet)
router.register("links", views.DeviceLinkViewSet)
router.register("rules", views.ModelRuleViewSet)
router.register("overrides", views.DeviceTypeOverrideViewSet)

urlpatterns = router.urls
