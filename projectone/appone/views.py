import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

# Never keep personal data in the browser URL (history, analytics, referrers).
PII_QUERY_KEYS = frozenset(
    {
        "name",
        "email",
        "contact",
        "city",
        "business",
        "demo_date",
        "demo_time",
        "application",
    }
)


def _empty_prefill():
    return {
        "name": "",
        "email": "",
        "contact": "",
        "city": "",
        "business": "",
        "demo_date": "",
        "demo_time": "",
        "applications": [],
    }


def _prefill_from_post(request):
    data = _empty_prefill()
    data["name"] = (request.POST.get("name") or "").strip()
    data["email"] = (request.POST.get("email") or "").strip()
    data["contact"] = (request.POST.get("contact") or "").strip()
    data["city"] = (request.POST.get("city") or "").strip()
    data["business"] = (request.POST.get("business") or "").strip()
    data["demo_date"] = (request.POST.get("demo_date") or "").strip()
    data["demo_time"] = (request.POST.get("demo_time") or "").strip()
    selected_app = (request.POST.get("application") or "").strip()
    data["applications"] = [selected_app] if selected_app else []
    return data


def index(request):
    get_keys = set(request.GET.keys())
    if get_keys & PII_QUERY_KEYS or request.GET.getlist("application"):
        request.session["lead_prefill"] = {
            "name": (request.GET.get("name") or "").strip(),
            "email": (request.GET.get("email") or "").strip(),
            "contact": (request.GET.get("contact") or "").strip(),
            "city": (request.GET.get("city") or "").strip(),
            "business": (request.GET.get("business") or "").strip(),
            "demo_date": (request.GET.get("demo_date") or "").strip(),
            "demo_time": (request.GET.get("demo_time") or "").strip(),
            "applications": request.GET.getlist("application"),
        }
        return redirect("/#hero-enquiry")

    prefill = {**_empty_prefill(), **request.session.pop("lead_prefill", {})}
    if "applications" not in prefill or not isinstance(prefill["applications"], list):
        prefill["applications"] = []
    apps = [a for a in prefill["applications"] if a]
    prefill["selected_application"] = apps[0] if apps else ""

    submitted = bool(request.session.pop("lead_submitted", False))

    return render(
        request,
        "appone/index.html",
        {
            "prefill": prefill,
            "submitted": submitted,
        },
    )


@require_http_methods(["POST"])
def submit_lead(request):
    """Forward Book Demo form to moltres-institute.com CRM webhook."""
    selected_app = (request.POST.get("application") or "").strip()
    applications = [selected_app] if selected_app else []
    if not applications:
        messages.error(request, "Please select an application.")
        request.session["lead_prefill"] = _prefill_from_post(request)
        return redirect("/#hero-enquiry")

    demo_date = (request.POST.get("demo_date") or "").strip()
    demo_time = (request.POST.get("demo_time") or "").strip()
    if not demo_date or not demo_time:
        messages.error(request, "Please select demo date and time.")
        request.session["lead_prefill"] = _prefill_from_post(request)
        return redirect("/#hero-enquiry")

    demo_datetime = f"{demo_date}T{demo_time}:00+05:30"

    payload = {
        "full_name": (request.POST.get("name") or "").strip(),
        "email": (request.POST.get("email") or "").strip(),
        "mobile": (request.POST.get("contact") or "").strip(),
        "city": (request.POST.get("city") or "").strip(),
        "business_name": (request.POST.get("business") or "").strip(),
        "applications": applications,
        "demo_datetime": demo_datetime,
        "demo_slot": demo_time,
        "demo_date": demo_date,
        "demo_time": demo_time,
        "source": "moltres-infotech.com",
    }

    webhook_url = getattr(
        settings,
        "MOLTRES_CRM_WEBHOOK_URL",
        "https://www.moltres-institute.com/moltres-infotech/api/webhook/",
    )
    webhook_secret = getattr(settings, "MOLTRES_CRM_WEBHOOK_SECRET", "")

    if not webhook_secret:
        logger.error("MOLTRES_CRM_WEBHOOK_SECRET is not configured")
        messages.error(
            request,
            "Lead capture is temporarily unavailable. Please email info@moltresinnovations.com",
        )
        return redirect("/#hero-enquiry")

    body = json.dumps(payload).encode("utf-8")
    req = Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Moltres-Webhook-Key": webhook_secret,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=25) as response:
            status = response.status
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        logger.exception("CRM webhook request failed: %s", exc)
        messages.error(request, "Could not reach CRM. Please try again in a moment.")
        return redirect("/#hero-enquiry")

    if status in (200, 201):
        request.session["lead_submitted"] = True
        return redirect("/#hero-enquiry")

    logger.warning("CRM webhook rejected lead: %s %s", status, raw[:500])
    try:
        detail = json.loads(raw).get("error", "CRM rejected the submission.")
    except (ValueError, TypeError):
        detail = "CRM rejected the submission."
    messages.error(request, detail)
    request.session["lead_prefill"] = _prefill_from_post(request)
    return redirect("/#hero-enquiry")
