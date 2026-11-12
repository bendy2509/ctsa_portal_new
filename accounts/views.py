from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .forms import (
    LoginForm,
    ClientRegistrationForm,
    ProfileUpdateForm,
    StaffUserCreateForm,
    StaffUserUpdateForm,
)
from .models import User
from .utils import safe_next_url

# Create your views here.


def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_staff_member:
            return redirect('terminal:dashboard')
        return redirect('terminal:client_watchlist')

    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.user_cache
        login(request, user)
        messages.success(request, f"Bienvenue sur CTSA Portal, {user.display_name}.")
        # Retour vers la page d'origine (ex. la fiche du conteneur recherché), si elle est interne.
        next_url = safe_next_url(request, request.GET.get('next', ''), '')
        if next_url:
            return redirect(next_url)
        if user.is_staff_member:
            return redirect('terminal:dashboard')
        return redirect('terminal:client_watchlist')

    return render(request, 'accounts/login.html', {'form': form})


def register_view(request):
    if request.user.is_authenticated:
        return redirect('terminal:client_watchlist')

    form = ClientRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(
            request,
            "Votre compte client Cap Terminal S.A. a été créé avec succès."
        )
        return redirect('terminal:client_watchlist')

    return render(request, 'accounts/register.html', {'form': form})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "Vous avez été déconnecté en toute sécurité.")
    return redirect('terminal:home')


@login_required
def profile_view(request):
    profile_form = ProfileUpdateForm(instance=request.user)
    password_form = PasswordChangeForm(user=request.user)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_profile':
            profile_form = ProfileUpdateForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Vos informations de profil ont été mises à jour.")
                return redirect('accounts:profile')
        elif action == 'change_password':
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Votre mot de passe a été modifié avec succès.")
                return redirect('accounts:profile')

    return render(request, 'accounts/profile.html', {
        'profile_form': profile_form,
        'password_form': password_form,
    })


@login_required
def user_list_view(request):
    if not request.user.is_portal_admin:
        messages.error(request, "Accès réservé aux administrateurs Cap Terminal S.A.")
        return redirect('terminal:dashboard')

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

    create_form = StaffUserCreateForm()
    if request.method == 'POST':
        create_form = StaffUserCreateForm(request.POST)
        if create_form.is_valid():
            new_user = create_form.save()
            messages.success(
                request,
                f"L'utilisateur « {new_user.display_name} » ({new_user.get_role_display()}) a été créé."
            )
            return redirect('accounts:user_list')

    return render(request, 'portal/users.html', {
        'users': users,
        'create_form': create_form,
        'role_choices': User.Role.choices,
        'role_filter': role_filter,
        'q': q,
    })


@login_required
def user_edit_view(request, pk):
    if not request.user.is_portal_admin:
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('terminal:dashboard')

    target_user = get_object_or_404(User, pk=pk)
    form = StaffUserUpdateForm(request.POST or None, instance=target_user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f"L'utilisateur « {target_user.display_name} » a été mis à jour.")
        return redirect('accounts:user_list')

    return render(request, 'portal/user_edit.html', {
        'target_user': target_user,
        'form': form,
    })


@login_required
@require_POST
def user_delete_view(request, pk):
    if not request.user.is_portal_admin:
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('terminal:dashboard')

    target_user = get_object_or_404(User, pk=pk)
    if target_user.pk == request.user.pk:
        messages.error(request, "Vous ne pouvez pas supprimer votre propre compte administrateur actif.")
        return redirect('accounts:user_list')

    name = target_user.display_name
    target_user.delete()
    messages.success(request, f"Le compte utilisateur « {name} » a été supprimé.")
    return redirect('accounts:user_list')

