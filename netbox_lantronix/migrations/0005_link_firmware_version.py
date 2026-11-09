from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_lantronix", "0004_portfacts_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="devicelink",
            name="firmware_version",
            field=models.CharField(
                blank=True,
                help_text="Running firmware reported by Percepxion",
                max_length=32,
                verbose_name="Firmware version (Percepxion)",
            ),
        ),
    ]
