from django.urls import include, path
from utilities.urls import get_model_urls

from . import views  # noqa: F401  registers the model views

app_name = "netbox_lantronix"

urlpatterns = [
    path("sources/", include(get_model_urls(app_name, "percepxionsource", detail=False))),
    path("sources/<int:pk>/", include(get_model_urls(app_name, "percepxionsource"))),
    path("links/", include(get_model_urls(app_name, "devicelink", detail=False))),
    path("links/<int:pk>/", include(get_model_urls(app_name, "devicelink"))),
    path("rules/", include(get_model_urls(app_name, "modelrule", detail=False))),
    path("rules/<int:pk>/", include(get_model_urls(app_name, "modelrule"))),
    path("overrides/", include(get_model_urls(app_name, "devicetypeoverride", detail=False))),
    path("overrides/<int:pk>/", include(get_model_urls(app_name, "devicetypeoverride"))),
    path("findings/", include(get_model_urls(app_name, "syncfinding", detail=False))),
]
