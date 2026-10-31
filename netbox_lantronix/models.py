from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.http import urlencode
from netbox.models import NetBoxModel
from netbox.models.features import JobsMixin
from netbox.plugins import get_plugin_config
from utilities.choices import ChoiceSet
from utilities.querysets import RestrictedQuerySet


class LinkStatusChoices(ChoiceSet):
    ACTIVE = "active"
    PENDING = "pending"

    CHOICES = (
        (ACTIVE, "Active", "green"),
        (PENDING, "Pending approval", "orange"),
    )


class TypeSourceChoices(ChoiceSet):
    OVERRIDE = "override"
    SYSCONFIG = "sysconfig"
    RULE = "rule"
    PORTS = "ports"

    CHOICES = (
        (OVERRIDE, "Device override"),
        (SYSCONFIG, "show sysconfig"),
        (RULE, "Model rule"),
        (PORTS, "Port counts"),
    )


class FindingKindChoices(ChoiceSet):
    CONFLICT = "conflict"
    AMBIGUOUS_MODEL = "ambiguous_model"
    NO_DEVICE_TYPE = "no_device_type"
    ADOPTION_PENDING = "adoption_pending"
    REJECTED_NAME = "rejected_name"
    OUT_OF_ORG = "out_of_org"
    MISSING = "missing"
    DUPLICATE_NAME = "duplicate_name"

    CHOICES = (
        (CONFLICT, "Conflict", "red"),
        (AMBIGUOUS_MODEL, "Ambiguous model", "orange"),
        (NO_DEVICE_TYPE, "No device type", "orange"),
        (ADOPTION_PENDING, "Adoption pending", "blue"),
        (REJECTED_NAME, "Rejected name", "red"),
        (OUT_OF_ORG, "Outside organization", "gray"),
        (MISSING, "Missing from Percepxion", "yellow"),
        (DUPLICATE_NAME, "Duplicate name", "red"),
    )


class PercepxionSource(JobsMixin, NetBoxModel):
    """One Percepxion tenant connection. Credentials live in plugin config, never here."""

    name = models.CharField(max_length=100, unique=True)
    endpoint = models.CharField(
        max_length=100,
        help_text="Name of an endpoint in PLUGINS_CONFIG['netbox_lantronix']['endpoints']",
    )
    organization_id = models.CharField(max_length=64, verbose_name="Percepxion organization ID")
    site = models.ForeignKey(to="dcim.Site", on_delete=models.PROTECT, related_name="+")
    role = models.ForeignKey(to="dcim.DeviceRole", on_delete=models.PROTECT, related_name="+")
    sync_interval = models.PositiveIntegerField(
        default=60, help_text="Minutes between scheduled syncs; 0 disables the schedule"
    )
    ssh_base_port = models.PositiveIntegerField(default=3000)
    percepxion_link = models.BooleanField(default=False, verbose_name="Show 'Open in Percepxion'")
    enabled = models.BooleanField(default=True)
    backoff_until = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ("name",)
        verbose_name = "Percepxion source"
        # NetBox resolves "<action>_<model>"; grant action "sync" in an object permission.
        permissions = (("sync_percepxionsource", "Can run a Percepxion sync"),)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("plugins:netbox_lantronix:percepxionsource", args=[self.pk])

    def clean(self):
        super().clean()
        if self.endpoint not in (get_plugin_config("netbox_lantronix", "endpoints") or {}):
            raise ValidationError(
                {"endpoint": "No endpoint with this name in plugin configuration."}
            )
        if self.sync_interval and self.sync_interval < 5:
            raise ValidationError({"sync_interval": "Use 0 (off) or at least 5 minutes."})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # A save through the UI or API keeps the schedule only if that user may sync this
        # source; otherwise scheduled runs are suspended. Saves outside a request
        # (shell, migrations) leave the schedule alone.
        from netbox.context import current_request

        from .jobs import apply_schedule_for

        request = current_request.get()
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            apply_schedule_for(self, user)

    def schedule_sync(self, user):
        """(Re)schedule this source's periodic sync. Callers check the sync permission first."""
        from .jobs import schedule

        return schedule(self, user)

    @property
    def endpoint_config(self):
        return (get_plugin_config("netbox_lantronix", "endpoints") or {}).get(self.endpoint)


class DeviceLink(NetBoxModel):
    """Authoritative tie between a NetBox device and a Percepxion device ID."""

    source = models.ForeignKey(PercepxionSource, on_delete=models.PROTECT, related_name="links")
    device = models.OneToOneField(
        to="dcim.Device", on_delete=models.CASCADE, related_name="percepxion_link"
    )
    percepxion_device_id = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20, choices=LinkStatusChoices, default=LinkStatusChoices.ACTIVE
    )
    type_source = models.CharField(max_length=20, choices=TypeSourceChoices, blank=True)
    mgmt_ip = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Management IP (Percepxion)",
        help_text="Reported by Percepxion; used for Open console when the device has no primary IP",
    )
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("source", "device")
        constraints = (
            models.UniqueConstraint(
                fields=("source", "percepxion_device_id"), name="%(app_label)s_link_unique_pxid"
            ),
        )
        permissions = (("approve_devicelink", "Can approve a pending device adoption"),)

    def __str__(self):
        return f"{self.device} ({self.percepxion_device_id})"

    def get_absolute_url(self):
        return reverse("plugins:netbox_lantronix:devicelink", args=[self.pk])

    def get_status_color(self):
        return LinkStatusChoices.colors.get(self.status)


class DeviceTypeOverride(NetBoxModel):
    source = models.ForeignKey(PercepxionSource, on_delete=models.CASCADE, related_name="overrides")
    percepxion_device_id = models.CharField(max_length=64)
    device_type = models.ForeignKey(
        to="dcim.DeviceType", on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ("source", "percepxion_device_id")
        constraints = (
            models.UniqueConstraint(
                fields=("source", "percepxion_device_id"),
                name="%(app_label)s_override_unique_pxid",
            ),
        )

    def __str__(self):
        return f"{self.percepxion_device_id} -> {self.device_type}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_lantronix:devicetypeoverride", args=[self.pk])

    def clean(self):
        super().clean()
        if self.device_type_id and self.device_type.manufacturer.slug != "lantronix":
            raise ValidationError({"device_type": "Must be a Lantronix device type."})


class ModelRule(NetBoxModel):
    """Per-source answer for a hardware signature Percepxion can't resolve on its own."""

    source = models.ForeignKey(PercepxionSource, on_delete=models.CASCADE, related_name="rules")
    model = models.CharField(
        max_length=32, help_text="Model as Percepxion reports it, e.g. SLC9016"
    )
    rj45_ports = models.PositiveIntegerField()
    usb_ports = models.PositiveIntegerField()
    device_type = models.ForeignKey(
        to="dcim.DeviceType", on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ("source", "model", "rj45_ports", "usb_ports")
        constraints = (
            models.UniqueConstraint(
                fields=("source", "model", "rj45_ports", "usb_ports"),
                name="%(app_label)s_rule_unique_signature",
            ),
        )

    def __str__(self):
        return f"{self.model} {self.rj45_ports}/{self.usb_ports} -> {self.device_type}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_lantronix:modelrule", args=[self.pk])

    def clean(self):
        super().clean()
        from .percepxion.resolver import resolve_sku

        if self.model:
            candidates = resolve_sku(self.model, self.rj45_ports, self.usb_ports)
            if not candidates:
                raise ValidationError(
                    {"model": "No Lantronix device type matches this model and port counts."}
                )
            if self.device_type_id and self.device_type.slug not in candidates:
                raise ValidationError(
                    {"device_type": f"Not a candidate for this signature: {', '.join(candidates)}"}
                )


class SyncFinding(models.Model):
    """A reported item from one run. Plain model: not change-logged, pruned by the job."""

    source = models.ForeignKey(PercepxionSource, on_delete=models.CASCADE, related_name="findings")
    job = models.ForeignKey(
        to="core.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=30, choices=FindingKindChoices)
    device_name = models.CharField(max_length=64)
    percepxion_device_id = models.CharField(max_length=64, blank=True)
    detail = models.CharField(max_length=500, blank=True)
    # Hardware signature of an ambiguous model, so the UI can offer "Create rule".
    model = models.CharField(max_length=32, blank=True)
    rj45_ports = models.PositiveIntegerField(null=True, blank=True)
    usb_ports = models.PositiveIntegerField(null=True, blank=True)

    objects = RestrictedQuerySet.as_manager()

    def rule_add_query(self):
        """Query string that pre-fills the model rule form, or "" when there's no signature."""
        if not self.model or self.rj45_ports is None or self.usb_ports is None:
            return ""
        return urlencode(
            {
                "source": self.source_id,
                "model": self.model,
                "rj45_ports": self.rj45_ports,
                "usb_ports": self.usb_ports,
                "return_url": self.source.get_absolute_url(),
            }
        )

    def override_add_query(self):
        if not self.percepxion_device_id:
            return ""
        return urlencode(
            {
                "source": self.source_id,
                "percepxion_device_id": self.percepxion_device_id,
                "return_url": self.source.get_absolute_url(),
            }
        )

    class Meta:
        ordering = ("-created", "kind", "device_name")

    def __str__(self):
        return f"{self.get_kind_display()}: {self.device_name}"

    def get_kind_color(self):
        return FindingKindChoices.colors.get(self.kind)


class PortFacts(NetBoxModel):
    """Attached-device facts Percepxion reports for one console server port."""

    port = models.OneToOneField(
        to="dcim.ConsoleServerPort", on_delete=models.CASCADE, related_name="percepxion_facts"
    )
    make = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    serial = models.CharField(max_length=100, blank=True)
    os = models.CharField(max_length=100, blank=True)
    os_version = models.CharField(max_length=100, blank=True)
    # Discovery state from the telemetry view; attached=False keeps the facts above as the
    # last device seen on the port.
    state = models.CharField(max_length=30, blank=True)
    attached = models.BooleanField(default=True)
    last_reported = models.DateTimeField()

    class Meta:
        ordering = ("port",)
        verbose_name_plural = "port facts"

    def __str__(self):
        return f"{self.port}: {self.make} {self.model}".strip()

    def get_absolute_url(self):
        return self.port.get_absolute_url()
