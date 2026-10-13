"""The web app's side of calendars and contacts on phones: the device login (Settings → Phone sync)."""
from fastapi import APIRouter, Request

from app.core.db import connection
from app.core.secretbox import unseal
from app.dav import devices
from app.identity.auth import User
from app.identity.oauth import public_base

router = APIRouter()


def _own_address(request: Request) -> str:
    return public_base(request).rstrip("/") + "/"


@router.get("/api/dav")
def dav_info(request: Request, user=User):
    with connection() as conn:
        return devices.info(conn, user["id"], _own_address(request))


@router.post("/api/dav/check")
def dav_check(request: Request, user=User):
    """Test the public CalDAV address the way a phone would."""
    with connection() as conn:
        devices.ensure(conn, user["id"])
        acc = conn.execute("SELECT username, password FROM caldav_accounts WHERE user_id = %s AND is_builtin", (user["id"],)).fetchone()
    return devices.check_public(acc["username"], unseal(acc["password"]), _own_address(request))


@router.post("/api/dav/reset")
def dav_reset(request: Request, user=User):
    """Replace the user's device password (devices set up with the old one stop syncing)."""
    with connection() as conn:
        devices.provision(conn, user["id"])
        return devices.info(conn, user["id"], _own_address(request))


@router.get("/api/dav/devices")
def list_devices(user=User):
    """Phones and apps that used the device login: what they are, when they last synced and last sent a change."""
    with connection() as conn:
        return devices.list_devices(conn, user["id"])


@router.delete("/api/dav/devices/{device_id}")
def forget_device(device_id: str, user=User):
    """Take a device off the list (it's back if it connects again; a new device password stops it)."""
    with connection() as conn:
        devices.forget(conn, user["id"], device_id)
    return {"ok": True}
