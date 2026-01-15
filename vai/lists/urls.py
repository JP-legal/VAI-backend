from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LeadListViewSet

router = DefaultRouter()
router.register(r'lists', LeadListViewSet, basename='leadlist')

urlpatterns = [
    path('', include(router.urls)),
]