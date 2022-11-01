from django.contrib import admin
from django.urls import reverse
from django.utils.safestring import mark_safe

# Register your models here.
from feeds import models

class SourceAdmin(admin.ModelAdmin):

    readonly_fields = (
        'posts_link',
    )

    def posts_link(self, obj=None):
        if obj.id is None:
            return ''
        qs = obj.posts.all()
        return mark_safe(
            '<a href="%s?source__id=%i" target="_blank">%i Posts</a>' % (
                reverse('admin:feeds_post_changelist'), obj.id, qs.count()
            )
        )
    posts_link.short_description = 'posts'

class PostAdmin(admin.ModelAdmin):

    raw_id_fields = ('source',)

    list_display = ('title', 'source', 'created', 'guid', 'author')

    search_fields = ('title',)

    readonly_fields = (
        'enclosures_link',
    )

    def enclosures_link(self, obj=None):
        if obj.id is None:
            return ''
        qs = obj.enclosures.all()
        return mark_safe(
            '<a href="%s?post__id=%i" target="_blank">%i Enclosures</a>' % (
                reverse('admin:feeds_enclosure_changelist'), obj.id, qs.count()
            )
        )
    enclosures_link.short_description = 'enclosures'

class EnclosureAdmin(admin.ModelAdmin):

    raw_id_fields = ('post',)

    list_display = ('href', 'type')

admin.site.register(models.Source, SourceAdmin)
admin.site.register(models.Post, PostAdmin)
admin.site.register(models.Enclosure, EnclosureAdmin)
admin.site.register(models.WebProxy)
