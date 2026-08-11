from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("resumes", "0004_jobresumecuration"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobresumecuration",
            name="candidate_data_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="jobresumecuration",
            name="gap_analysis_data",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="jobresumecuration",
            name="gap_analysis_error_code",
            field=models.CharField(blank=True, max_length=80),
        ),
    ]
