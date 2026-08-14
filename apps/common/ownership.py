"""
Reusable ownership/access-control for job-based PDF features (Merge, Split,
and anything built the same way in future).

Architectural decision - documented here, not just in a commit message:

This product has no user accounts, login, or session system today (no
registration/login page exists anywhere in the frontend; no auth endpoints
exist in the backend beyond Django's own /admin/). Bolting on a full
username/password or OAuth login flow to "fix" file access would be a much
bigger, unasked-for product decision - a login UI, password reset, email
verification, etc. - so this deliberately does NOT do that.

What it does instead: every job-based feature issues a random, unguessable
per-job bearer token at creation time (see generate_owner_token()), returned
only in that creation response. Whoever holds that exact token can access
that one job's status/download; nobody else can - including another
anonymous visitor who happens to see or guess the job's UUID. This closes
the actual gap that existed before this layer: previously, a job's UUID
alone (routinely visible in URLs, logs, and API responses) was sufficient
to download ANY job's output, from any client, with no secret required at
all. A bearer token is a strict improvement over that with zero login
friction, and slots in cleanly once real user accounts exist (see below).

Forward compatibility: `job.user` (nullable FK, already present on every
job model) remains the primary ownership check for authenticated requests.
The token is the fallback that makes anonymous jobs private too. When a
real auth system is added, `is_owner()` needs no changes - authenticated
owners already win the check.

Non-existence and wrong-token cases are intentionally indistinguishable to
the caller (both surface as a generic 404) - confirming "this id exists,
you just have the wrong token" would let an attacker enumerate valid job
ids even without ever accessing their contents.
"""

import secrets

TOKEN_QUERY_PARAM = "token"
TOKEN_HEADER = "HTTP_X_OWNER_TOKEN"


def generate_owner_token():
    return secrets.token_urlsafe(32)


def get_request_token(request):
    """Accept the owner token via query param (needed for plain browser
    navigation / <a href> downloads, which can't attach headers) or the
    X-Owner-Token header (for programmatic/API clients)."""
    return request.query_params.get(TOKEN_QUERY_PARAM) or request.META.get(TOKEN_HEADER, "")


def is_owner(request, job):
    """
    True if this request is allowed to access `job` (a model instance with
    nullable `user` and `owner_token` fields).
    """
    if request.user.is_authenticated and job.user_id == request.user.id:
        return True

    provided = get_request_token(request)
    if not provided or not job.owner_token:
        return False

    return secrets.compare_digest(provided, job.owner_token)
