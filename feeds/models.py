
import datetime
from urllib.parse import urlencode
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.conf import settings
from django.core.paginator import Paginator, EmptyPage, InvalidPage
from django.db import models
from django.db.models import Q
import django.utils as django_utils
from django.utils.deconstruct import deconstructible


@deconstructible
class ExpiresGenerator(object):
    """Callable Key Generator that returns a random keystring.
    """

    def __call__(self):
        return django_utils.timezone.now() - datetime.timedelta(days=1)


class Source(models.Model):
    """This class represents a Feed to be read.

        It really should have been called Feed, but what can you do?
    """
    name = models.CharField(max_length=255, blank=True, null=True)
    """**str** The name of the Feed (automatically populated)"""

    site_url = models.CharField(max_length=255, blank=True, null=True)
    """**str** url of the website associated with the feed (automatically populated)"""

    alt_url = models.URLField(blank=True, null=True)

    feed_url = models.CharField(max_length=512)
    """**str** The URL that will be fetched to read the feed"""

    image_url = models.CharField(max_length=512, blank=True, null=True)
    """**str** The url of an image representing the feed (automatically populated)"""

    description = models.TextField(null=True, blank=True)
    """**str** The site description: may be HTML, be careful (automatically populated)"""

    last_polled = models.DateTimeField(blank=True, null=True)
    """**datetime** The last time the Feed was fetched"""

    due_poll = models.DateTimeField(default=datetime.datetime(1900, 1, 1))  # default to distant past to put new sources to front of queue
    """**datetime** When the Feed is next due to be fetched"""

    etag = models.CharField(max_length=255, blank=True, null=True)
    last_modified = models.CharField(max_length=255, blank=True, null=True)  # just pass this back and forward between server and me , no need to parse

    last_result = models.CharField(max_length=255, blank=True, null=True)
    """**str** The result the last fetch"""

    interval = models.PositiveIntegerField(default=400)
    """**int** How often the Feed will be fetched in minutes"""

    last_success = models.DateTimeField(blank=True, null=True)
    """**datetime** When the Feed was last read successfully"""

    last_change = models.DateTimeField(blank=True, null=True)
    """**datetime** When the Feed last changed"""

    live = models.BooleanField(default=True)
    """**bool** Is the Feed being actively fetched"""

    status_code = models.PositiveIntegerField(default=0)
    last_302_url = models.CharField(max_length=512, null=True, blank=True)
    last_302_start = models.DateTimeField(null=True, blank=True)

    max_index = models.IntegerField(default=0)
    last_read = models.IntegerField(default=0)
    num_subs = models.IntegerField(default=1)

    json = models.JSONField(null=True, blank=True)
    """**dict** Raw information about the Feed in JSON format (will not be collected unless **FEEDS_SAVE_JSON** is set to **True** in settings)"""

    is_cloudflare = models.BooleanField(default=False)
    """**bool** Is this feed being hindered bt Cloudflare?"""

    def __str__(self):
        return self.display_name

    @property
    def subscriber_count(self):
        """**int** he number of subscribers this feed has"""
        return self.num_subs

    @property
    def unread_count(self):
        """**int** In a single user system how many unread articles are there?

        If you need more than one user, or want to arrange feeds
        into folders, use a Subscription
        """
        return self.max_index - self.last_read

    @property
    def best_link(self):
        """**str** The best user facing link to this feed.

        Will be the **site_url** if it's present, otherwise **feed_url**
        """
        if self.site_url is None or self.site_url == '':
            return self.feed_url
        else:
            return self.site_url

    @property
    def display_name(self):
        """**str** The best user-facing name for this feed.

        Will be the the feed's **name** as described in the feed if there is one.
        Otherwise it will be the **best_link**
        """
        if self.name is None or self.name == "":
            return self.best_link
        else:
            return self.name

    @property
    def garden_style(self):
        """Visual representation of how health the feed is Green -> Red

        Internal to FeedThing and Recast and should probably be moved
        """

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
        """Visual representation of how health the feed is Green -> Red

        Internal to FeedThing and Recast and should probably be moved
        """

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

    def get_unread_posts(self, oldest_first=False):
        """**ResultSet[Post]** In a single user system get all unread posts

        If you need more than one user, or want to arrange feeds
        into folders, use a Subscription
        """

        if oldest_first:
            return self.posts.filter(index__gt=self.last_read)
        else:
            return self.post.filter(index__gt=self.last_read).order_by("-created")

    def get_paginated_posts(self, page: int, oldest_first: bool = False, posts_per_page: int = 20):
        """Get a posts from the feed a page at a time

        :param page: The page to fetch.
        :type page: int

        :param oldest_first: Get the posts in reverse chronological order (default True)
        :type oldest_first: bool

        :param posts_per_page: The number of posts per page (default 20)
        :type posts_per_page: int

        :return: A tuple containting the page of posts and the paginator
        :rtype: Tuple[List[Post], Paginator]
        """

        post_list = Post.objects.filter(source=self)
        if oldest_first:
            post_list = post_list.order_by("-created")

        paginator = Paginator(post_list, posts_per_page)

        try:
            posts = paginator.page(page)
        except (EmptyPage, InvalidPage):
            posts = paginator.page(1)

        return (posts, paginator)

    def mark_read(self):
        """In a single user system, mark this feed as read
        """
        self.last_read = self.max_index
        self.save()

    def update_subscriber_count(self):
        """Called by the django save / delete hooks to update num_subs

        Internal method, there should be no need to call this
        """

        self.num_subs = Subscription.objects.filter(source=self).count()
        self.save()

    """Will this appear in the docs?"""


class Post(models.Model):
    """An entry in a feed

    """

    GUID_MAX_LENGTH = 768
    # an entry in a feed

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name='posts')
    """**Source** The source feed that this post belongs to"""

    title = models.TextField(blank=True)
    """**str** The post title"""

    body = models.TextField()
    """**str** The main content of the feed in html or plain text"""

    link = models.CharField(max_length=512, blank=True, null=True)
    """**str** Link to this post on the web"""

    found = models.DateTimeField(auto_now_add=True)
    """**datetime** When this post was first discovered"""

    created = models.DateTimeField(db_index=True)
    """**datetime** The created date for this post as reported in the feed"""

    guid = models.CharField(max_length=GUID_MAX_LENGTH, blank=True, null=True, db_index=True)
    """**str** The unique ID for this post"""

    author = models.CharField(max_length=255, blank=True, null=True)
    """**str** Name of the author of this post as reported by the feed"""

    index = models.IntegerField(db_index=True)
    """**int** The number of this post in the feed for the purposes of tracking read/unread state"""

    image_url = models.CharField(max_length=512, blank=True, null=True)
    """**str** The URL of an image that represents this post"""

    json = models.JSONField(null=True, blank=True)
    """**dict** Raw information about the Post in JSON format (will not be collected unless **FEEDS_SAVE_JSON** is set to **True** in settings)"""

    @property
    def current_enclosures(self):
        """**ResultSet[Enclosure]** Returns all the current enclosures for this post"""
        return self.enclosures.filter(is_current=True)

    @property
    def old_enclosures(self):
        """**ResultSet[Enclosure]**  Returns all the previous enclosures for this post

        Some feeds change the URL of enclosures between reads.  By default
        old enclosures are deleted and new ones added each time the feed is polled.
        To keep references to old enclosures set **FEEDS_KEEP_OLD_ENCLOSURES** to **True**
        in settings.
        """
        return self.enclosures.filter(is_current=False)

    @property
    def title_url_encoded(self):
        # Why does this even exist?
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

    def save(self, *args, **kwargs):
        if self.index is None:
            self.index = self.source.max_index + 1
            self.source.max_index = self.index
            self.source.save(update_fields=["max_index"])
        super().save(*args, **kwargs)


class Enclosure(models.Model):
    """An enclosure on a post

    """
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='enclosures')
    """**Post** The Post that this Enclosure belongs to"""

    length = models.IntegerField(default=0)
    """**int** Size in bytes of the the related file"""

    href = models.CharField(max_length=512)
    """**url** The url of the enclosure"""
    # TODO rename this to URL

    type = models.CharField(max_length=256)
    """**str** The type of the enclosure"""

    medium = models.CharField(max_length=25, null=True, blank=True)
    """**str** The type of the enclosure.  Almost certainly one of image/video/audio"""

    description = models.CharField(max_length=512, null=True, blank=True)
    """**str** A description of the enclosure - e.g. Alt text on an image"""

    is_current = models.BooleanField(default=True)
    """**bool** Is this enclosure current (if we are saving old enclosures - see above)."""

    @property
    def recast_link(self):

        # TODO: This needs to come out, it's just for recast
        return "/enclosure/%d/" % self.id

    @property
    def is_image(self):
        """**bool** Is the enclosure an image?"""
        if self.medium == "image":
            return True
        return "image/" in self.type and not self.medium

    @property
    def is_audio(self):
        """**bool** Is the enclosure audio?"""
        if self.medium == "audio":
            return True
        return "audio/" in self.type and not self.medium

    @property
    def is_video(self):
        """**bool** Is the enclosure video?"""
        if self.medium == "video":
            return True
        return "video/" in self.type and not self.medium


# A user subscription
class Subscription(models.Model):
    """A subscription to a Source Feed by a User

        Subscriptions are also the way folder structures are set up
    """
    def __str__(self):
        return "'%s' for user %s" % (self.name, str(self.user))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    """**User** The owner of the Subscription"""

    source = models.ForeignKey(Source, blank=True, null=True, on_delete=models.CASCADE, related_name='subscriptions')  # null source means we are a folder
    """**Source** The source feed of the subscription.  If this is **None** then this is actually a folder"""

    parent = models.ForeignKey('self', blank=True, null=True, on_delete=models.CASCADE, related_name='subscriptions')
    """**Subscription** The parent folder of the subscription.  **None** if the subscription is at the root leve"""

    last_read = models.IntegerField(default=0)

    is_river = models.BooleanField(default=False)
    """**bool** Indicates if the feed/folder should be displayed in a "River of News" style"""
    name = models.CharField(max_length=255)

    @property
    def unread_count(self):
        """**int** The number of undread posts in teh subscription

            If the subscription is acting as a folder, this will total
            up the unread counts of all children
        """
        if self.source:
            return self.source.max_index - self.last_read
        else:
            if not hasattr(self, "_unread_count"):
                self._unread_count = 0
                for child in Subscription.objects.filter(parent=self):
                    self._unread_count += child.unread_count

            return self._unread_count

    def _gather_posts(self, post_list):

        if self.source:
            posts = list(Post.objects.filter(Q(source=self.source) & Q(index__gt=self.last_read)))
            for post in posts:
                post.from_subscription = self
                post_list.append(post)

        for child in Subscription.objects.filter(parent=self):
            child._gather_posts(post_list)

    def get_unread_posts(self, oldest_first=True):
        """ Returns all the unread posts in a subscription"""
        posts = []
        self._gather_posts(posts)
        posts.sort(reverse=(not oldest_first), key=lambda post: post.created)  # Sort in ascending order
        return posts

    def _gather_sources(self, source_list: dict):

        if self.source:
            source_list[self.source.id] = self

        for child in Subscription.objects.filter(parent=self):
            child._gather_sources(source_list)

    def get_paginated_posts(self, page: int, oldest_first: bool =True, posts_per_page: int = 20):
        """Get a posts from the feed a page at a time

        :param page: The page to fetch.
        :type page: int

        :param posts_per_page: The number of posts per page (default 20)
        :type posts_per_page: int

        :return: A tuple containting the page of posts and the paginator
        :rtype: Tuple[List[Post], Paginator]
        """

        sources = {}
        self._gather_sources(sources)

        source_ids = list(sources.keys())
        post_list = Post.objects.filter(source_id__in=source_ids).order_by("-created")

        paginator = Paginator(post_list, posts_per_page)

        try:
            posts = paginator.page(page)
        except (EmptyPage, InvalidPage):
            posts = paginator.page(1)

        for p in posts:
            p.subscription = sources[p.source_id]

        return (posts, paginator)

    def mark_read(self):
        """Marks all the posts in the subscription as read

        If the subscription is acting as a folder then it will mark all
        children as read as well.
        """
        if self.source:
            self.last_read = self.source.max_index
            self.save()
        else:
            # I am a folder
            for child in Subscription.objects.filter(parent=self):
                child.mark_read()


@receiver(post_delete)
def delete_subscriber(sender, instance, **kwargs):
    if sender == Subscription and instance.source is not None:
        instance.source.update_subscriber_count()


@receiver(post_save)
def save_subscriber(sender, instance, **kwargs):
    if sender == Subscription and instance.source is not None:
        instance.source.update_subscriber_count()
