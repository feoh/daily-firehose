#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'daily_firehose.settings')
    if 'test' in sys.argv[1:2]:
        # Access and job records are production signals; a test run that emitted
        # one per request would bury its own failures. assertLogs still raises
        # the level of whichever logger a test inspects.
        os.environ.setdefault('DJANGO_LOG_LEVEL', 'WARNING')
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
