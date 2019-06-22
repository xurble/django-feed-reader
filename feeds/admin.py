from django.contrib import admin

# Register your models here.
from feeds import models

admin.site.register(models.Source)
admin.site.register(models.Post)
admin.site.register(models.Enclosure)
admin.site.register(models.WebProxy)