import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from .models import DeviceLink, DeviceTypeOverride, ModelRule, PercepxionSource, SyncFinding


class PercepxionSourceTable(NetBoxTable):
    name = tables.Column(linkify=True)
    site = tables.Column(linkify=True)
    role = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()
    percepxion_link = columns.BooleanColumn(verbose_name="Percepxion link")
    links = tables.Column(accessor="links__count", verbose_name="Devices", orderable=False)

    class Meta(NetBoxTable.Meta):
        model = PercepxionSource
        fields = (
            "pk",
            "id",
            "name",
            "endpoint",
            "organization_id",
            "site",
            "role",
            "sync_interval",
            "enabled",
            "percepxion_link",
            "tags",
            "actions",
        )
        default_columns = ("name", "endpoint", "site", "role", "sync_interval", "enabled")


class DeviceLinkTable(NetBoxTable):
    device = tables.Column(linkify=True)
    source = tables.Column(linkify=True)
    status = columns.ChoiceFieldColumn()
    type_source = columns.ChoiceFieldColumn(verbose_name="Type from")
    actions = columns.ActionsColumn(actions=("delete",))

    class Meta(NetBoxTable.Meta):
        model = DeviceLink
        fields = (
            "pk",
            "id",
            "device",
            "source",
            "percepxion_device_id",
            "status",
            "type_source",
            "last_seen",
            "actions",
        )
        default_columns = (
            "device",
            "source",
            "percepxion_device_id",
            "status",
            "type_source",
            "last_seen",
        )


class ModelRuleTable(NetBoxTable):
    source = tables.Column(linkify=True)
    device_type = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = ModelRule
        fields = (
            "pk",
            "id",
            "source",
            "model",
            "rj45_ports",
            "usb_ports",
            "device_type",
            "actions",
        )
        default_columns = ("source", "model", "rj45_ports", "usb_ports", "device_type")


class DeviceTypeOverrideTable(NetBoxTable):
    source = tables.Column(linkify=True)
    device_type = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = DeviceTypeOverride
        fields = ("pk", "id", "source", "percepxion_device_id", "device_type", "actions")
        default_columns = ("source", "percepxion_device_id", "device_type")


class SyncFindingTable(NetBoxTable):
    source = tables.Column(linkify=True)
    kind = columns.ChoiceFieldColumn()
    fix = tables.TemplateColumn(
        template_name="netbox_lantronix/inc/finding_fix.html", verbose_name="", orderable=False
    )
    actions = None

    class Meta(NetBoxTable.Meta):
        model = SyncFinding
        fields = (
            "pk",
            "created",
            "source",
            "kind",
            "device_name",
            "percepxion_device_id",
            "detail",
            "fix",
        )
        default_columns = ("created", "source", "kind", "device_name", "detail", "fix")
