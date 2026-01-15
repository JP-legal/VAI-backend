from rest_framework.routers import DefaultRouter
from vai.campaigns.views import CampaignViewSet, CampaignOptionsViewSet, CallLogViewSet, AdminCallLogViewSet, \
    AdminSupportCallViewSet

router = DefaultRouter()
router.register(r"campaigns", CampaignViewSet, basename="campaign")
router.register(r"campaign-options", CampaignOptionsViewSet, basename="campaign-options")
router.register(r"calls", CallLogViewSet, basename="call")
router.register(r'admin/outbound-calls', AdminCallLogViewSet, basename='admin-outbound-calls')
router.register(r'admin/support-calls', AdminSupportCallViewSet, basename='admin-support-calls')
urlpatterns = router.urls