from django.urls import path

from . import views

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("setup/", views.setup, name="setup"),
    path("logout/", views.logout, name="logout"),
    path("channel/<str:channel>/", views.inbox, name="channel"),
    path("channel/<str:channel>/<str:contact>/", views.conversation, name="conversation"),
]
