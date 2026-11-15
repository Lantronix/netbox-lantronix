from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_lantronix", "0006_ssh_base_port_range"),
    ]

    operations = [
        migrations.AddField(
            model_name="devicelink",
            name="ssh_ports",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="SSH TCP port of each device port, from the unit's configuration in "
                "Percepxion. 0: SSH to the port is off. Empty: not read; the source's base port is used.",
                verbose_name="Console SSH ports",
            ),
        ),
    ]
