"""Create the NetBox objects the plugin depends on.

Reuses objects the lantronix.oob netbox_sync.yml playbook may already have created; never
modifies an existing object.
"""

MANUFACTURER = {"name": "Lantronix", "slug": "lantronix"}
PLATFORM = {"name": "Lantronix ITMG OS", "slug": "lantronix_itmgos"}
CUSTOM_FIELD = "percepxion_device_id"


def ensure_objects():
    """Return (manufacturer, platform, custom_field), creating any that are missing."""
    from core.models import ObjectType
    from dcim.models import Device, Manufacturer, Platform
    from extras.choices import CustomFieldTypeChoices
    from extras.models import CustomField

    manufacturer = Manufacturer.objects.filter(slug=MANUFACTURER["slug"]).first()
    if manufacturer is None:
        manufacturer = Manufacturer(**MANUFACTURER)
        manufacturer.full_clean()
        manufacturer.save()

    platform = Platform.objects.filter(slug=PLATFORM["slug"]).first()
    if platform is None:
        platform = Platform(manufacturer=manufacturer, **PLATFORM)
        platform.full_clean()
        platform.save()

    field = CustomField.objects.filter(name=CUSTOM_FIELD).first()
    if field is None:
        field = CustomField(
            name=CUSTOM_FIELD,
            label="Percepxion device ID",
            type=CustomFieldTypeChoices.TYPE_TEXT,
            description="Set by the Lantronix plugin or the lantronix.oob NetBox sync playbook",
        )
        field.save()
        field.object_types.set([ObjectType.objects.get_for_model(Device)])

    return manufacturer, platform, field
