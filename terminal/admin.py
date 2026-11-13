from django.contrib import admin
from .models import (
    ShippingLine,
    ContainerPrefix,
    Container,
    ContainerStatusLog,
    SavedContainer,
)

# Register your models here.


@admin.register(ShippingLine)
class ShippingLineAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'contact_phone', 'contact_email', 'is_active')
    search_fields = ('name', 'code', 'contact_email')
    list_filter = ('is_active',)


@admin.register(ContainerPrefix)
class ContainerPrefixAdmin(admin.ModelAdmin):
    list_display = ('prefix', 'shipping_line', 'description', 'created_at')
    search_fields = ('prefix', 'shipping_line__name', 'description')
    list_filter = ('shipping_line',)


class ContainerStatusLogInline(admin.TabularInline):
    model = ContainerStatusLog
    extra = 0
    readonly_fields = ('old_status', 'new_status', 'reason_text', 'changed_by', 'changed_at')
    can_delete = False


@admin.register(Container)
class ContainerAdmin(admin.ModelAdmin):
    list_display = (
        'container_number',
        'scheduled_date',
        'status',
        'shipping_line',
        'unavailability_note',
        'updated_by',
        'updated_at',
    )
    list_filter = ('scheduled_date', 'status', 'shipping_line')
    search_fields = ('container_number', 'unavailability_note', 'shipping_line__name')
    inlines = [ContainerStatusLogInline]


@admin.register(ContainerStatusLog)
class ContainerStatusLogAdmin(admin.ModelAdmin):
    list_display = ('container', 'old_status', 'new_status', 'reason_text', 'changed_by', 'changed_at')
    list_filter = ('new_status', 'changed_at')
    search_fields = ('container__container_number', 'reason_text')
    readonly_fields = ('container', 'old_status', 'new_status', 'reason_text', 'changed_by', 'changed_at')


@admin.register(SavedContainer)
class SavedContainerAdmin(admin.ModelAdmin):
    list_display = ('container_number', 'user', 'label', 'notify_whatsapp', 'created_at')
    search_fields = ('container_number', 'user__username', 'label')

