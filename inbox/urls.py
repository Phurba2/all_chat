from django.urls import path

from . import views

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("channel/<str:channel>/", views.inbox, name="channel"),
    path("c/<str:channel>/<str:contact>/", views.conversation, name="conversation"),
]
