from django.apps import AppConfig


class BlogConfig(AppConfig):
    name = 'blog'

    def ready(self):
        from . import checks  # noqa: F401  (registers the deploy check)
