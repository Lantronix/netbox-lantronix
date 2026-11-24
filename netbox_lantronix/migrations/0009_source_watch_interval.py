from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_lantronix", "0008_source_manage_cables"),
    ]

    operations = [
        migrations.AddField(
            model_name="percepxionsource",
            name="watch_interval",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Minutes between port-state checks that sync only the SLCs whose ports "
                "changed; 0 turns the watch off. Each check reads every linked SLC's port state.",
                verbose_name="Change watch interval",
            ),
        ),
    ]
