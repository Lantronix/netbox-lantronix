import django_filters
from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet

from .models import (
    DeviceLink,
    DeviceTypeOverride,
    FindingKindChoices,
    ModelRule,
    PercepxionSource,
    SyncFinding,
)


class PercepxionSourceFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = PercepxionSource
        fields = ("id", "name", "endpoint", "organization_id", "site", "role", "enabled")

    def search(self, queryset, name, value):
        return queryset.filter(Q(name__icontains=value) | Q(endpoint__icontains=value))


class DeviceLinkFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = DeviceLink
        fields = ("id", "source", "device", "percepxion_device_id", "status", "type_source")

    def search(self, queryset, name, value):
        return queryset.filter(
            Q(device__name__icontains=value) | Q(percepxion_device_id__icontains=value)
        )


class ModelRuleFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = ModelRule
        fields = ("id", "source", "model", "rj45_ports", "usb_ports", "device_type")

    def search(self, queryset, name, value):
        return queryset.filter(model__icontains=value)


class DeviceTypeOverrideFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = DeviceTypeOverride
        fields = ("id", "source", "percepxion_device_id", "device_type")

    def search(self, queryset, name, value):
        return queryset.filter(percepxion_device_id__icontains=value)


class SyncFindingFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method="search")
    kind = django_filters.MultipleChoiceFilter(choices=FindingKindChoices)

    class Meta:
        model = SyncFinding
        fields = ("id", "source", "kind", "job")

    def search(self, queryset, name, value):
        return queryset.filter(Q(device_name__icontains=value) | Q(detail__icontains=value))
