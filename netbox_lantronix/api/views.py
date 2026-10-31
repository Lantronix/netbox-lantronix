from netbox.api.viewsets import NetBoxModelViewSet

from .. import filtersets
from ..models import DeviceLink, DeviceTypeOverride, ModelRule, PercepxionSource
from . import serializers


class PercepxionSourceViewSet(NetBoxModelViewSet):
    queryset = PercepxionSource.objects.all()
    serializer_class = serializers.PercepxionSourceSerializer
    filterset_class = filtersets.PercepxionSourceFilterSet


class DeviceLinkViewSet(NetBoxModelViewSet):
    # Links are written by the sync; the API reads and deletes them.
    http_method_names = ["get", "head", "options", "delete"]
    queryset = DeviceLink.objects.all()
    serializer_class = serializers.DeviceLinkSerializer
    filterset_class = filtersets.DeviceLinkFilterSet


class ModelRuleViewSet(NetBoxModelViewSet):
    queryset = ModelRule.objects.all()
    serializer_class = serializers.ModelRuleSerializer
    filterset_class = filtersets.ModelRuleFilterSet


class DeviceTypeOverrideViewSet(NetBoxModelViewSet):
    queryset = DeviceTypeOverride.objects.all()
    serializer_class = serializers.DeviceTypeOverrideSerializer
    filterset_class = filtersets.DeviceTypeOverrideFilterSet
