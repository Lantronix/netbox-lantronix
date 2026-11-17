from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_lantronix", "0007_link_ssh_ports"),
    ]

    operations = [
        migrations.AddField(
            model_name="percepxionsource",
            name="manage_cables",
            field=models.BooleanField(
                default=False,
                help_text="Create and delete console cables on this source's SLC ports to match "
                "what each SLC reports. Percepxion becomes the source of truth for those cables.",
                verbose_name="Manage console cables",
            ),
        ),
    ]
