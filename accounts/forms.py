from django import forms
from django.contrib.auth import authenticate
from .models import User


class LoginForm(forms.Form):
    username = forms.CharField(
        label="Identifiant ou E-mail",
        max_length=150,
    )
    password = forms.CharField(
        label="Mot de passe",
        widget=forms.PasswordInput,
    )

    def __init__(self, *args, **kwargs):
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        identifier = (cleaned.get('username') or '').strip()
        password = cleaned.get('password') or ''
        if identifier and password:
            user = authenticate(username=identifier, password=password)
            if user is None:
                matched = User.objects.filter(email__iexact=identifier).first()
                if matched:
                    user = authenticate(username=matched.username, password=password)
            if user is None:
                raise forms.ValidationError(
                    "Identifiant ou mot de passe incorrect. Veuillez vérifier vos informations."
                )
            if not user.is_active:
                raise forms.ValidationError("Ce compte utilisateur est désactivé.")
            self.user_cache = user
        return cleaned


class ClientRegistrationForm(forms.ModelForm):
    password = forms.CharField(
        label="Mot de passe",
        min_length=8,
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = ['username', 'full_name', 'email', 'company_name', 'phone']

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            raise forms.ValidationError("Une adresse e-mail valide est requise.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cette adresse e-mail.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.CLIENT
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['full_name', 'email', 'company_name', 'phone']


class StaffUserCreateForm(forms.ModelForm):
    password = forms.CharField(
        label="Mot de passe initial",
        min_length=8,
        required=True,
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = ['username', 'full_name', 'email', 'role', 'company_name', 'phone', 'is_active']

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        user.is_staff = user.role == User.Role.ADMIN
        if commit:
            user.save()
        return user


class StaffUserUpdateForm(forms.ModelForm):
    new_password = forms.CharField(
        label="Nouveau mot de passe (laisser vide pour conserver)",
        min_length=8,
        required=False,
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = ['username', 'full_name', 'email', 'role', 'company_name', 'phone', 'is_active']

    def save(self, commit=True):
        user = super().save(commit=False)
        pwd = self.cleaned_data.get('new_password')
        if pwd:
            user.set_password(pwd)
        user.is_staff = user.role == User.Role.ADMIN or user.is_superuser
        if commit:
            user.save()
        return user
