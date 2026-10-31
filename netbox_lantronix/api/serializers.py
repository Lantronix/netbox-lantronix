from dcim.api.serializers import (
    ConsoleServerPortSerializer,
    DeviceRoleSerializer,
    DeviceSerializer,
    DeviceTypeSerializer,
    SiteSerializer,
)
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers

from ..models import DeviceLink, DeviceTypeOverride, ModelRule, PercepxionSource, PortFacts


class PercepxionSourceSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_lantronix-api:percepxionsource-detail"
    )
    site = SiteSerializer(nested=True)
    role = DeviceRoleSerializer(nested=True)

    class Meta:
        model = PercepxionSource
        fields = (
            "id",
            "url",
            "display",
            "name",
            "endpoint",
            "organization_id",
            "site",
            "role",
            "sync_interval",
            "ssh_base_port",
            "percepxion_link",
            "enabled",
            "backoff_until",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        read_only_fields = ("backoff_until",)
        brief_fields = ("id", "url", "display", "name")


class DeviceLinkSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_lantronix-api:devicelink-detail"
    )
    source = PercepxionSourceSerializer(nested=True, read_only=True)
    device = DeviceSerializer(nested=True, read_only=True)

    class Meta:
        model = DeviceLink
        fields = (
            "id",
            "url",
            "display",
            "source",
            "device",
            "percepxion_device_id",
            "status",
            "type_source",
            "mgmt_ip",
            "last_seen",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        read_only_fields = fields
        brief_fields = ("id", "url", "display", "percepxion_device_id", "status")


class ModelRuleSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_lantronix-api:modelrule-detail"
    )
    source = PercepxionSourceSerializer(nested=True)
    device_type = DeviceTypeSerializer(nested=True)

    class Meta:
        model = ModelRule
        fields = (
            "id",
            "url",
            "display",
            "source",
            "model",
            "rj45_ports",
            "usb_ports",
            "device_type",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "model")


class DeviceTypeOverrideSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_lantronix-api:devicetypeoverride-detail"
    )
    source = PercepxionSourceSerializer(nested=True)
    device_type = DeviceTypeSerializer(nested=True)

    class Meta:
        model = DeviceTypeOverride
        fields = (
            "id",
            "url",
            "display",
            "source",
            "percepxion_device_id",
            "device_type",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "percepxion_device_id")


class PortFactsSerializer(NetBoxModelSerializer):
    """Read-only shape for event rules and webhooks; PortFacts has no REST endpoint.

    NetBox serializes event data through get_serializer_for_model, so an event rule on
    PortFacts needs this class to exist. No url field: there is no detail view to link.
    """

    port = ConsoleServerPortSerializer(nested=True, read_only=True)
    slc = serializers.SerializerMethodField()
    hostname = serializers.SerializerMethodField()

    class Meta:
        model = PortFacts
        fields = (
            "id",
            "display",
            "port",
            "slc",
            "hostname",
            "make",
            "model",
            "serial",
            "os",
            "os_version",
            "state",
            "attached",
            "last_reported",
            "created",
            "last_updated",
        )
        read_only_fields = fields
        brief_fields = ("id", "display", "port", "model", "attached")

    def get_slc(self, obj):
        return obj.port.device.name if obj.port_id else ""

    def get_hostname(self, obj):
        # The sync writes the attached hostname to the port description; read it fresh so an
        # event serialized after the same run sees the current value.
        from dcim.models import ConsoleServerPort

        row = ConsoleServerPort.objects.filter(pk=obj.port_id).values_list("description", flat=True)
        return row.first() or ""
