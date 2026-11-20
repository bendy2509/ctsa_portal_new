"""Gestion des comptes utilisateurs de l'espace d'exploitation."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.forms import StaffUserCreateForm, StaffUserUpdateForm
from accounts.models import User
from accounts.permissions import Actions, peut, render_403, require_permission


def _log_user_audit(acting_user, target_user, category: str, old_value: str, new_value: str) -> None:
    """Enregistre un événement utilisateur dans le journal d'audit en ajout seulement."""
    from terminal.models import PortalAuditLog

    PortalAuditLog.objects.create(
        category=category,
        target_label=f"User:{target_user.username}",
        old_value=old_value,
        new_value=new_value,
        changed_by=acting_user,
    )


@require_permission(Actions.USER_MANAGE)
def user_list_view(request):
    role_filter = request.GET.get('role', '')
    q = request.GET.get('q', '').strip()
    users = User.objects.all().order_by('-date_joined')
    if role_filter:
        users = users.filter(role=role_filter)
    if q:
        users = users.filter(
            Q(username__icontains=q)
            | Q(full_name__icontains=q)
            | Q(email__icontains=q)
            | Q(company_name__icontains=q)
        )

    create_form = StaffUserCreateForm(acting_user=request.user)
    if request.method == 'POST':
        if request.POST.get('role') == User.Role.ADMIN and not request.user.is_superuser:
            return render_403(request, "Seul un Administrateur (Superadmin) peut créer un compte Administrateur.")
        create_form = StaffUserCreateForm(request.POST, acting_user=request.user)
        if create_form.is_valid():
            new_user = create_form.save()
            if new_user.role == User.Role.ADMIN:
                from terminal.models import PortalAuditLog
                _log_user_audit(
                    request.user, new_user, PortalAuditLog.Category.PROMOTE_SUPERADMIN,
                    "", "Administrateur (création)",
                )
            messages.success(
                request,
                f"L'utilisateur « {new_user.display_name} » ({new_user.get_role_display()}) a été créé."
            )
            return redirect('accounts:user_list')

    page = Paginator(users, 30).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)

    return render(request, 'portal/users.html', {
        'users': page,
        'page': page,
        'page_query': params.urlencode(),
        'create_form': create_form,
        'role_choices': User.Role.choices,
        'role_filter': role_filter,
        'q': q,
    })


@require_permission(Actions.USER_MANAGE)
def user_create_view(request):
    """Page dédiée de création d'un compte utilisateur."""
    if request.method == 'POST' and request.POST.get('role') == User.Role.ADMIN and not request.user.is_superuser:
        return render_403(request, "Seul un Administrateur (Superadmin) peut créer un compte Administrateur.")

    form = StaffUserCreateForm(request.POST or None, acting_user=request.user)
    if request.method == 'POST' and form.is_valid():
        new_user = form.save()
        if new_user.role == User.Role.ADMIN:
            from terminal.models import PortalAuditLog
            _log_user_audit(
                request.user, new_user, PortalAuditLog.Category.PROMOTE_SUPERADMIN,
                "", "Administrateur (création)",
            )
        messages.success(
            request,
            f"L'utilisateur « {new_user.display_name} » ({new_user.get_role_display()}) a été créé."
        )
        return redirect('accounts:user_list')

    return render(request, 'portal/user_create.html', {
        'form': form,
        'role_choices': form.fields['role'].choices,
    })


@require_permission(Actions.USER_MANAGE)
def user_edit_view(request, pk):
    target_user = get_object_or_404(User, pk=pk)
    if not peut(request.user, Actions.USER_MANAGE, target_user):
        return render_403(request, "Vous ne pouvez pas modifier un compte Superadmin.")

    if request.method == 'POST' and request.POST.get('role') == User.Role.ADMIN and not request.user.is_superuser:
        return render_403(request, "Seul un Administrateur (Superadmin) peut promouvoir un compte Administrateur.")

    old_role = target_user.role
    old_superuser = target_user.is_superuser
    form = StaffUserUpdateForm(request.POST or None, instance=target_user, acting_user=request.user)
    if request.method == 'POST' and form.is_valid():
        is_last_superadmin = (
            old_superuser
            and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target_user.pk).exists()
        )
        if is_last_superadmin and (
            not form.cleaned_data.get('is_active', True)
            or form.cleaned_data.get('role') != User.Role.ADMIN
        ):
            messages.error(request, "Impossible de désactiver ou rétrograder le dernier Administrateur actif.")
            return render(request, 'portal/user_edit.html', {
                'target_user': target_user,
                'form': form,
            })

        updated_user = form.save()
        if old_role != updated_user.role:
            from terminal.models import PortalAuditLog
            _log_user_audit(
                request.user, updated_user, PortalAuditLog.Category.ROLE_CHANGE,
                old_role, updated_user.role,
            )
        messages.success(request, f"L'utilisateur « {target_user.display_name} » a été mis à jour.")
        return redirect('accounts:user_list')

    return render(request, 'portal/user_edit.html', {
        'target_user': target_user,
        'form': form,
    })


@require_permission(Actions.USER_MANAGE)
@require_POST
def user_delete_view(request, pk):
    target_user = get_object_or_404(User, pk=pk)
    if not peut(request.user, Actions.USER_MANAGE, target_user):
        return render_403(request, "Vous ne pouvez pas supprimer un compte Superadmin.")

    if target_user.pk == request.user.pk:
        messages.error(request, "Vous ne pouvez pas supprimer votre propre compte administrateur actif.")
        return redirect('accounts:user_list')

    if (
        target_user.is_superuser
        and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target_user.pk).exists()
    ):
        messages.error(request, "Impossible de supprimer le dernier Superadmin actif.")
        return redirect('accounts:user_list')

    name = target_user.display_name
    target_user.delete()
    messages.success(request, f"Le compte utilisateur « {name} » a été supprimé.")
    return redirect('accounts:user_list')
