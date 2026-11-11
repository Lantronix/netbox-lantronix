import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_lantronix", "0005_link_firmware_version"),
    ]

    operations = [
        migrations.AlterField(
            model_name="percepxionsource",
            name="ssh_base_port",
            field=models.PositiveIntegerField(
                default=3000,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(65000),
                ],
            ),
        ),
    ]
