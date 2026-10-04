"""Who is really on the other end of a request.

Production traffic reaches Django through two hops we control (the site's
edge worker, then nginx on the server). Each hop appends the address it
received the connection from to X-Forwarded-For, so the *last* entries are
written by our own infrastructure and the *first* ones by whoever sent the
request. Taking the first entry, as this code once did, lets anyone choose
their own "IP" by sending a header: every per-IP limit could be sidestepped
by changing it on each attempt.

The reliable reading is from the right: skip the addresses that belong to
our own infrastructure (the edge network's published ranges and private
addresses), and the first one left is the address the edge saw. Anything
further left is client-supplied and ignored.
"""

import ipaddress

from rest_framework.throttling import ScopedRateThrottle

# Cloudflare's published ranges (https://www.cloudflare.com/ips/). The edge
# worker's requests to the API originate from here, so these are hops of
# ours, never the visitor.
_EDGE_NETWORKS = [
    ipaddress.ip_network(cidr)
    for cidr in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
        "2400:cb00::/32",
        "2606:4700::/32",
        "2803:f800::/32",
        "2405:b500::/32",
        "2405:8100::/32",
        "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
]


def _is_our_infrastructure(address):
    if address.is_private or address.is_loopback or address.is_link_local:
        return True
    return any(address in network for network in _EDGE_NETWORKS if network.version == address.version)


def client_ip(request):
    """Best-effort address of the visitor. Never raises."""
    try:
        remote = request.META.get("REMOTE_ADDR") or None
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        for part in reversed([p.strip() for p in forwarded.split(",") if p.strip()]):
            try:
                address = ipaddress.ip_address(part)
            except ValueError:
                continue
            if not _is_our_infrastructure(address):
                return str(address)
        return remote
    except Exception:  # pragma: no cover - defensive
        return None


class ClientIPScopedRateThrottle(ScopedRateThrottle):
    """ScopedRateThrottle that buckets anonymous callers by their real
    address rather than by a header they can rewrite (or, behind a proxy, by
    the proxy's own address, which would put every visitor in one bucket)."""

    def get_ident(self, request):
        return client_ip(request) or super().get_ident(request)
