"""Test-only WebSocket ping producer for the #170 WS-responsiveness harness.

Relays a sentinel notification to the ``"global"`` channel group so a connected
``AnnotationStateConsumer`` re-emits it to its socket. The harness stamps the
HTTP-send time and the WS-receipt time CLIENT-SIDE and correlates by ``seq``,
measuring WS push latency under load (iossifovlab/gain#170).

Gated behind ``settings.LOADTEST_PING_ENABLED`` (true only under
``settings_e2e``) so it can never appear in a production URLconf. It re-uses the
consumer's existing ``annotation_notify`` handler, so NO production consumer
change is needed. The view is SYNC: it runs on a ``thread_sensitive`` thread
(reachable even while the loop is busy), and its ``async_to_sync(group_send)``
hop onto the loop carries any loop-contention into the measured window.
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def loadtest_ping(request: HttpRequest) -> HttpResponse:
    """Group-send a sentinel ping to the ``global`` group; return 200."""
    seq = request.GET.get("seq", "0")
    channel_layer = get_channel_layer()
    assert channel_layer is not None, "No channel layer configured"
    async_to_sync(channel_layer.group_send)(
        "global",
        {"type": "annotation_notify", "message": f"loadtest_ping:{seq}"},
    )
    return HttpResponse("ok")
