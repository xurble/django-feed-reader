
from django.core.management.base import BaseCommand

from feeds.utils import update_feeds


class Command(BaseCommand):
    """
        This command refreshes the RSS feeds

        Usage is ``python manage.py refreshfeeds``

    """

    help = 'Refreshes the RSS feeds, 30 at a time'

    def handle(self, *args, **options):

        update_feeds(30)

        self.stdout.write(self.style.SUCCESS('\nFinished'))
