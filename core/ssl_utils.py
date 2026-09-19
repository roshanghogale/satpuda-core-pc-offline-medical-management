"""SSL CA bundle helpers for HTTPS in dev and PyInstaller EXE builds."""
from __future__ import annotations

import os
import sys

_configured = False


def ca_bundle_path() -> str:
    if getattr(sys, 'frozen', False):
        bundled = os.path.join(getattr(sys, '_MEIPASS', ''), 'certifi', 'cacert.pem')
        if os.path.isfile(bundled):
            return bundled
    try:
        import certifi
        path = certifi.where()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return ''


def configure_ssl_certificates() -> str:
    """
    Point HTTPS libraries at a valid CA bundle (critical for EXE + Win7).

    Safe to call multiple times.
    """
    global _configured
    cafile = ca_bundle_path()
    if cafile:
        os.environ['SSL_CERT_FILE'] = cafile
        os.environ['REQUESTS_CA_BUNDLE'] = cafile
        os.environ['CURL_CA_BUNDLE'] = cafile
    _configured = True
    return cafile


def ssl_context():
    import ssl
    cafile = ca_bundle_path()
    if cafile:
        return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


def httplib2_http():
    """Google API client HTTP object with bundled CA certs (needed on Win7 EXE)."""
    import httplib2
    cafile = ca_bundle_path()
    if cafile:
        return httplib2.Http(ca_certs=cafile)
    return httplib2.Http()
