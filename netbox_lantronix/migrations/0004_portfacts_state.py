from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_lantronix", "0003_link_mgmt_ip"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfacts",
            name="state",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="portfacts",
            name="attached",
            field=models.BooleanField(default=True),
        ),
    ]
