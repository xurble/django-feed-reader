
from django.core.management.base import BaseCommand, CommandError

from feeds.utils import update_feeds

class Command(BaseCommand):
    """
        This command refreshes the RSS feeds
    """


    help = 'Rrefreshes the RSS feeds'

    def handle(self, *args, **options):

        update_feeds(30)

        self.stdout.write(self.style.SUCCESS('Finished'))
