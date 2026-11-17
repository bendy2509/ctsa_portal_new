from django.contrib import admin
from .models import (
    ShippingLine,
    ContainerPrefix,
    Motif,
    Container,
    VerificationEvent,
    ContainerStatusLog,
    SavedContainer,
    PortalAuditLog,
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


@admin.register(Motif)
class MotifAdmin(admin.ModelAdmin):
    list_display = ('type', 'label', 'display_order', 'is_active', 'is_system', 'requires_note')
    list_filter = ('type', 'is_active', 'is_system')
    search_fields = ('label',)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VerificationEvent)
class VerificationEventAdmin(admin.ModelAdmin):
    list_display = ('container', 'result', 'reason', 'created_by', 'created_at', 'archived_at', 'is_historical')
    list_filter = ('result', 'is_historical', 'archived_at')
    search_fields = ('container__container_number', 'note')


@admin.register(PortalAuditLog)
class PortalAuditLogAdmin(admin.ModelAdmin):
    list_display = ('changed_at', 'category', 'target_label', 'old_value', 'new_value', 'changed_by')
    list_filter = ('category', 'changed_at')
    search_fields = ('target_label', 'old_value', 'new_value', 'reason')
    readonly_fields = ('category', 'target_label', 'old_value', 'new_value', 'reason', 'changed_by', 'changed_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

