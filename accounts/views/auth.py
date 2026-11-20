"""Vues d'authentification (hors périmètre de modification)."""

from django.contrib import messages
from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.forms import ClientRegistrationForm, LoginForm
from accounts.utils import safe_next_url


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
