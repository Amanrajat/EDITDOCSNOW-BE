"""
Consistent response envelope for every PDF-processing feature's API.

    success_response(...) -> {"success": true, "message": ..., "data": {...}}
    error_response(...)   -> {"success": false, "message": ..., "error_code": ...}
"""

from rest_framework.response import Response


def success_response(message, data=None, status_code=200):
    payload = {"success": True, "message": message}

    if data is not None:
        payload["data"] = data

    return Response(payload, status=status_code)


def error_response(message, error_code="ERROR", status_code=400, errors=None):
    payload = {"success": False, "message": message, "error_code": error_code}

    if errors is not None:
        payload["errors"] = errors

    return Response(payload, status=status_code)
