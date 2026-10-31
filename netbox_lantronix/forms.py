from dcim.models import Device, DeviceRole, DeviceType, Site
from django import forms
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from netbox.plugins import get_plugin_config
from utilities.forms.fields import DynamicModelChoiceField
from utilities.forms.rendering import FieldSet

from .models import DeviceTypeOverride, ModelRule, PercepxionSource
from .percepxion.resolver import resolve_sku


def _endpoint_choices():
    return [
        (name, name) for name in sorted(get_plugin_config("netbox_lantronix", "endpoints") or {})
    ]


class PercepxionSourceForm(NetBoxModelForm):
    endpoint = forms.ChoiceField(choices=_endpoint_choices)
    site = DynamicModelChoiceField(queryset=Site.objects.all())
    role = DynamicModelChoiceField(queryset=DeviceRole.objects.all())

    fieldsets = (
        FieldSet("name", "endpoint", "organization_id", "enabled", name="Source"),
        FieldSet("site", "role", name="New devices"),
        FieldSet("sync_interval", "ssh_base_port", "percepxion_link", name="Sync and console"),
        FieldSet("tags", name="Tags"),
    )

    class Meta:
        model = PercepxionSource
        fields = (
            "name",
            "endpoint",
            "organization_id",
            "enabled",
            "site",
            "role",
            "sync_interval",
            "ssh_base_port",
            "percepxion_link",
            "tags",
        )


class PercepxionSourceFilterForm(NetBoxModelFilterSetForm):
    model = PercepxionSource


class _LantronixTypeField(DynamicModelChoiceField):
    def __init__(self, **kwargs):
        super().__init__(
            queryset=DeviceType.objects.filter(manufacturer__slug="lantronix"),
            query_params={"manufacturer": "lantronix"},
            **kwargs,
        )


class ModelRuleForm(NetBoxModelForm):
    source = DynamicModelChoiceField(queryset=PercepxionSource.objects.all())
    device_type = _LantronixTypeField()

    class Meta:
        model = ModelRule
        fields = ("source", "model", "rj45_ports", "usb_ports", "device_type", "tags")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Opened from an ambiguous-model finding: offer only that signature's candidate types.
        values = self.data or self.initial
        model, rj45, usb = (values.get(k) for k in ("model", "rj45_ports", "usb_ports"))
        candidates = resolve_sku(model, rj45, usb) if model else []
        if candidates:
            field = self.fields["device_type"]
            field.queryset = field.queryset.filter(slug__in=candidates)
            field.widget.add_query_params({"slug": candidates})
            field.help_text = "Candidates for this signature: " + ", ".join(candidates)


class DeviceTypeOverrideForm(NetBoxModelForm):
    source = DynamicModelChoiceField(queryset=PercepxionSource.objects.all())
    device_type = _LantronixTypeField()

    class Meta:
        model = DeviceTypeOverride
        fields = ("source", "percepxion_device_id", "device_type", "tags")


class DeviceSelectForm(forms.Form):
    device = DynamicModelChoiceField(queryset=Device.objects.all())
