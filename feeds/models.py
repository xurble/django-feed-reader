
import datetime
from urllib.parse import urlencode
import logging


from django.conf import settings
from django.db import models
import django.utils as django_utils
from django.utils.deconstruct import deconstructible


@deconstructible
class ExpiresGenerator(object):
    """Callable Key Generator that returns a random keystring.
    """

    def __call__(self):
        return django_utils.timezone.now() - datetime.timedelta(days=1)


class Source(models.Model):
    # This is an actual feed that we poll
    name = models.CharField(max_length=255, blank=True, null=True)
    site_url = models.CharField(max_length=255, blank=True, null=True)
    feed_url = models.CharField(max_length=512)
    image_url = models.CharField(max_length=512, blank=True, null=True)

    description = models.TextField(null=True, blank=True)

    last_polled = models.DateTimeField(blank=True, null=True)
    due_poll = models.DateTimeField(default=datetime.datetime(1900, 1, 1))  # default to distant past to put new sources to front of queue
    etag = models.CharField(max_length=255, blank=True, null=True)
    last_modified = models.CharField(max_length=255, blank=True, null=True)  # just pass this back and forward between server and me , no need to parse

    last_result = models.CharField(max_length=255, blank=True, null=True)
    interval = models.PositiveIntegerField(default=400)
    last_success = models.DateTimeField(blank=True, null=True)
    last_change = models.DateTimeField(blank=True, null=True)
    live = models.BooleanField(default=True)
    status_code = models.PositiveIntegerField(default=0)
    last_302_url = models.CharField(max_length=512, null=True, blank=True)
    last_302_start = models.DateTimeField(null=True, blank=True)

    max_index = models.IntegerField(default=0)
    last_read = models.IntegerField(default=0)
    num_subs = models.IntegerField(default=1)

    is_cloudflare = models.BooleanField(default=False)

    def __str__(self):
        return self.display_name

    def mark_read(self):
        """
            In a single user system, marm this feed as read
        """
        self.last_read = self.max_index
        self.save()

    @property
    def unread_count(self):
        """
            In a single user system how many unread articles are there?

            If you need more than one user, or want to arrange feeds
            into folders, use a Subscription (below)
        """
        return self.max_index - self.last_read

    @property
    def best_link(self):
        # the html link else the feed link
        if self.site_url is None or self.site_url == '':
            return self.feed_url
        else:
            return self.site_url

    @property
    def display_name(self):
        if self.name is None or self.name == "":
            return self.best_link
        else:
            return self.name

    @property
    def garden_style(self):

        if not self.live:
            css = "background-color:#ccc;"
        elif self.last_change is None or self.last_success is None:
            css = "background-color:#D00;color:white"
        else:
            dd = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc) - self.last_change

            days = int(dd.days / 2)

            col = 255 - days
            if col < 0:
                col = 0

            css = "background-color:#ff%02x%02x" % (col, col)

            if col < 128:
                css += ";color:white"

        return css

    @property
    def health_box(self):

        if not self.live:
            css = "#ccc;"
        elif self.last_change is None or self.last_success is None:
            css = "#F00;"
        else:
            dd = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc) - self.last_change

            days = int(dd.days/2)

            red = days
            if red > 255:
                red = 255

            green = 255 - days
            if green < 0:
                green = 0

            css = "#%02x%02x00" % (red, green)

        return css


class Post(models.Model):
    GUID_MAX_LENGTH = 768
    # an entry in a feed

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name='posts')
    title = models.TextField(blank=True)
    body = models.TextField()
    link = models.CharField(max_length=512, blank=True, null=True)
    found = models.DateTimeField(auto_now_add=True)
    created = models.DateTimeField(db_index=True)
    guid = models.CharField(max_length=GUID_MAX_LENGTH, blank=True, null=True, db_index=True)
    author = models.CharField(max_length=255, blank=True, null=True)
    index = models.IntegerField(db_index=True)
    image_url = models.CharField(max_length=512, blank=True, null=True)

    @property
    def title_url_encoded(self):
        try:
            ret = urlencode({"X": self.title})
            if len(ret) > 2:
                ret = ret[2:]
        except Exception:
            logging.info("Failed to url encode title of post {}".format(self.id))
            ret = ""

    def __str__(self):
        return "%s: post %d, %s" % (self.source.display_name, self.index, self.title)

    @property
    def recast_link(self):

        # TODO: This needs to come out, it's just for recast
        return "/post/%d/" % self.id

    class Meta:
        ordering = ["index"]


class Enclosure(models.Model):

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='enclosures')
    length = models.IntegerField(default=0)
    href = models.CharField(max_length=512)
    type = models.CharField(max_length=256)
    medium = models.CharField(max_length=25, null=True, blank=True)
    description = models.CharField(max_length=512, null=True, blank=True)

    @property
    def recast_link(self):

        # TODO: This needs to come out, it's just for recast

        return "/enclosure/%d/" % self.id

    @property
    def is_image(self):
        if self.medium == "image":
            return True
        return "image/" in self.type and not self.medium

    @property
    def is_audio(self):
        if self.medium == "audio":
            return True
        return "audio/" in self.type and not self.medium

    @property
    def is_video(self):
        if self.medium == "video":
            return True
        return "video/" in self.type and not self.medium


# A user subscription
class Subscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source = models.ForeignKey(Source, blank=True, null=True, on_delete=models.CASCADE, related_name='subscriptions')  # null source means we are a folder
    parent = models.ForeignKey('self', blank=True, null=True, on_delete=models.CASCADE, related_name='subscriptions')
    last_read = models.IntegerField(default=0)
    is_river = models.BooleanField(default=False)
    name = models.CharField(max_length=255)

    def __str__(self):
        return "'%s' for user %s" % (self.name, str(self.user))

    def mark_read(self):
        if self.source:
            self.last_read = self.source.max_index
            self.save()
        else:
            # I am a folder
            for child in Subscription.objects.filter(parent=self):
                child.mark_read()

    @property
    def unread_count(self):
        if self.source:
            return self.source.max_index - self.last_read
        else:
            if not hasattr(self, "_unread_count"):
                self._unread_count = 0
                for child in Subscription.objects.filter(parent=self):
                    self._unread_count += child.unread_count

            return self._unread_count


class WebProxy(models.Model):
    # this class if for Cloudflare avoidance and contains a list of potential
    # web proxies that we can try, scraped from the internet
    address = models.CharField(max_length=255)

    def __str__(self):
        return "Proxy:{}".format(self.address)
