from django.db import models


class SystemConfiguration(models.Model):
    """
    Dynamic system settings to override values in settings.py.
    """
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    description = models.TextField(blank=True)
    category = models.CharField(max_length=50)
    value_type = models.CharField(max_length=20)  # string, integer, float, boolean, list
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} = {self.value} ({self.value_type})"

    def parsed_value(self):
        val = self.value
        t = self.value_type
        if t == "integer":
            try:
                return int(val)
            except ValueError:
                return 0
        elif t == "float":
            try:
                return float(val)
            except ValueError:
                return 0.0
        elif t == "boolean":
            return val.strip().lower() in ("true", "yes", "1", "t", "y")
        elif t == "list":
            return [item.strip() for item in val.split(",") if item.strip()]
        return val

