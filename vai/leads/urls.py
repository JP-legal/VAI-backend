from rest_framework.routers import DefaultRouter
from .views import LeadViewSet, AdminLeadViewSet

router = DefaultRouter()
router.register("leads", LeadViewSet, basename="lead")
router.register("admin/leads", AdminLeadViewSet, basename="admin-lead")
urlpatterns = router.urls