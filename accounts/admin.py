from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User

# Register your models here.


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'full_name', 'email', 'role', 'company_name', 'phone', 'is_active')
    list_filter = ('role', 'is_active', 'is_staff')
    search_fields = ('username', 'full_name', 'email', 'company_name', 'phone')
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Informations CTSA Portal', {'fields': ('role', 'full_name', 'company_name', 'phone')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Informations CTSA Portal', {'fields': ('role', 'full_name', 'email', 'company_name', 'phone')}),
    )

