"""Coupon management for staff. Distinct from CouponRedeemAPIView (billing_view.py),
which is what an end user hits to redeem a code — this is what an admin uses to mint
and retire codes in the first place, previously only possible via the
`create_coupon` management command or raw Django admin.
"""

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.models import Coupon
from billing.serializers.coupon_admin_serializer import CouponCreateSerializer, CouponSerializer


class AdminCouponListCreateAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        coupons = Coupon.objects.all().order_by("-created_at")
        return Response(CouponSerializer(coupons, many=True).data)

    def post(self, request):
        serializer = CouponCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        coupon = serializer.save()
        return Response(CouponSerializer(coupon).data, status=status.HTTP_201_CREATED)


class AdminCouponToggleActiveAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, coupon_id):
        try:
            coupon = Coupon.objects.get(id=coupon_id)
        except Coupon.DoesNotExist:
            return Response({"detail": "Coupon not found."}, status=status.HTTP_404_NOT_FOUND)

        coupon.is_active = not coupon.is_active
        coupon.save(update_fields=["is_active"])
        return Response(CouponSerializer(coupon).data)
