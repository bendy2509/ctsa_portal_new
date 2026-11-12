from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('connexion/', views.login_view, name='login'),
    path('inscription/', views.register_view, name='register'),
    path('deconnexion/', views.logout_view, name='logout'),
    path('profil/', views.profile_view, name='profile'),
    path('admin-users/', views.user_list_view, name='user_list'),
    path('admin-users/<int:pk>/modifier/', views.user_edit_view, name='user_edit'),
    path('admin-users/<int:pk>/supprimer/', views.user_delete_view, name='user_delete'),
]
