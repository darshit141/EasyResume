"""Earned-credit status and the rewarded-ad reward flow.

Three endpoints:

* ``GET  /api/billing/credits/``                     — balance + whether an
  ad can be watched right now, and the client config for whichever provider
  the operator selected.
* ``POST /api/billing/credits/rewarded-ad/ticket/``  — issues the signed
  ticket the browser passes into the ad SDK; pre-checks the daily cap.
* ``GET|POST /api/billing/credits/rewarded-ad/callback/`` — the ad
  network's server-to-server reward postback. Unauthenticated by session:
  the provider's signature is the authentication. Verified, deduplicated,
  capped, then a CreditGrant is written.
"""

import logging

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import User
from billing.entitlements import is_paid_plan
from billing.services.credit_service import CreditService, RewardedAdCapReachedError
from billing.services.entitlements_service import EntitlementsService
from billing.services.rewarded_ad_providers import (
    RewardedAdNotConfiguredError,
    RewardedAdVerificationError,
    get_rewarded_ad_provider,
    issue_ticket,
)

logger = logging.getLogger(__name__)


def _rewarded_ad_state(user, provider):
    """The shared block describing whether this user can earn a credit by
    watching an ad, plus what the browser needs to show one."""
    # Any paying tier, not just Pro: a hardcoded == "pro" here would have
    # started offering ad-for-credit deals to Max subscribers, who pay the
    # most precisely so they don't see ads.
    is_paid = is_paid_plan(EntitlementsService.for_user(user)["plan_id"])
    configured = provider.is_configured()
    watched_today = CreditService.rewarded_ads_watched_today(user)
    cap = CreditService.rewarded_ads_daily_cap()
    can_watch = configured and not is_paid and CreditService.can_watch_rewarded_ad(user)
    return {
        "enabled": configured and not is_paid,
        "provider": provider.name if configured else None,
        "client_config": provider.client_config() if can_watch else {},
        "credits_per_view": CreditService.credits_per_rewarded_ad(),
        "watched_today": watched_today,
        "daily_cap": cap,
        "can_watch_now": can_watch,
    }


class CreditsStatusAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        provider = get_rewarded_ad_provider()
        balance = CreditService.active_balance(user)
        next_expiry = CreditService.next_expiry(user)
        return Response(
            {
                "balance": balance,
                "next_expiry": next_expiry.isoformat() if next_expiry else None,
                "rewarded_ad": _rewarded_ad_state(user, provider),
            }
        )


class RewardedAdTicketAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        provider = get_rewarded_ad_provider()
        state = _rewarded_ad_state(user, provider)
        if not state["can_watch_now"]:
            return Response(
                {
                    "detail": "You can't earn a credit from an ad right now.",
                    "code": "REWARDED_AD_UNAVAILABLE",
                    **state,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {
                "ticket": issue_ticket(user.id),
                "provider": provider.name,
                "client_config": provider.client_config(),
                "credits_per_view": state["credits_per_view"],
            }
        )


class RewardedAdCallbackAPIView(APIView):
    """Server-to-server. Google Ad Manager sends a GET; a partner network may
    send a GET or a POSTed form. Either way the provider adapter verifies the
    signature and tells us who to credit."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def _handle(self, request):
        provider = get_rewarded_ad_provider()
        params = {key: request.GET.get(key) for key in request.GET.keys()}
        if request.method == "POST":
            for key in request.POST.keys():
                params.setdefault(key, request.POST.get(key))
        # The exact bytes matter for signature schemes that sign the raw
        # query string (Google's does).
        params["__raw_query__"] = request.META.get("QUERY_STRING", "")

        try:
            reward = provider.verify_callback(
                method=request.method, params=params, body=request.body or b""
            )
        except RewardedAdNotConfiguredError:
            return Response({"detail": "Rewarded ads are not enabled."}, status=status.HTTP_404_NOT_FOUND)
        except RewardedAdVerificationError as exc:
            logger.warning("Rejected rewarded-ad callback: %s", exc)
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        user = User.objects.filter(id=reward.user_id).first()
        if user is None:
            logger.warning("Rewarded-ad callback for unknown user id %s", reward.user_id)
            # 200 so the network doesn't retry a callback that can never succeed.
            return Response({"detail": "No such user."}, status=status.HTTP_200_OK)

        try:
            grant, created = CreditService.grant_rewarded_ad_credit(
                user,
                amount=reward.amount,
                provider=reward.provider,
                external_id=reward.external_id,
            )
        except RewardedAdCapReachedError:
            # The user genuinely watched, but they're over the daily cap.
            # 200 so the network treats it as delivered and doesn't retry.
            return Response({"detail": "Daily limit reached."}, status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        logger.info(
            "Rewarded-ad credit %s for user %s (txn %s, +%s)",
            "granted" if created else "already applied",
            user.id,
            reward.external_id,
            grant.amount,
        )
        return Response({"status": "ok", "credited": grant.amount, "duplicate": not created})

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)
