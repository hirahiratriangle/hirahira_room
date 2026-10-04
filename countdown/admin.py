from django.contrib import admin

from .models import CountEvent, ReminderLog


@admin.register(CountEvent)
class CountEventAdmin(admin.ModelAdmin):
    list_display = ('name', 'date', 'user', 'notify', 'created_at')
    list_filter = ('user', 'notify')
    search_fields = ('name', 'memo')


@admin.register(ReminderLog)
class ReminderLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'sent_on', 'event_count', 'sent_at')
    list_filter = ('sent_on',)
    search_fields = ('user__username',)
    readonly_fields = ('sent_at',)
