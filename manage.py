#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    # ローカル開発では config.settings_dev（DEBUG/SQLite）を既定にする。
    # Azure App Service 上（WEBSITE_HOSTNAME あり）では従来どおり本番設定を使う。
    # DJANGO_SETTINGS_MODULE を明示すればそちらが優先される。
    default_settings = (
        'config.settings' if 'WEBSITE_HOSTNAME' in os.environ else 'config.settings_dev'
    )
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', default_settings)
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
