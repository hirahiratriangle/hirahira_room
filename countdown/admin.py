from django.contrib import admin

from .models import CountEvent


@admin.register(CountEvent)
class CountEventAdmin(admin.ModelAdmin):
    list_display = ('name', 'date', 'user', 'created_at')
    list_filter = ('user',)
    search_fields = ('name', 'memo')
