# Generated by Django 3.1.2 on 2020-11-04 10:23

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feeds', '0008_auto_20201027_1623'),
    ]

    operations = [
        migrations.AlterField(
            model_name='post',
            name='guid',
            field=models.CharField(blank=True, db_index=True, max_length=512, null=True),
        ),
    ]