
from django.core.management.base import BaseCommand, CommandError

from ft.reader import update_feeds

class Command(BaseCommand):
    help = 'Rrefreshes the RSS feeds'

    def handle(self, *args, **options):

        update_feeds(self.stdout, "http://ft.xurble.org", 30)

        self.stdout.write(self.style.SUCCESS('Finished'))