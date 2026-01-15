from rest_framework.routers import DefaultRouter
from .views import PhoneNumberRequestViewSet, AdminPhoneNumberViewSet

router = DefaultRouter()
router.register(r"phone-numbers", PhoneNumberRequestViewSet, basename="phone-number-requests")
router.register(r"admin/phone-numbers", AdminPhoneNumberViewSet, basename="admin-phone-number")

urlpatterns = router.urls