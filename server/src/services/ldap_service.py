import logging

logger = logging.getLogger(__name__)


async def test_ldap_connection(config: dict) -> tuple[bool, str]:
    server_url = config.get("server_url", "")
    bind_dn = config.get("bind_dn", "")
    bind_password = config.get("bind_password", "")
    base_dn = config.get("base_dn", "")

    if not server_url or not bind_dn or not bind_password:
        return False, "Missing required fields: server_url, bind_dn, bind_password"

    try:
        import ldap3

        server = ldap3.Server(server_url, get_info=ldap3.ALL)
        conn = ldap3.Connection(
            server, user=bind_dn, password=bind_password, auto_bind=True
        )
        if conn.bound:
            if base_dn:
                conn.search(base_dn, "(objectClass=*)", search_scope=ldap3.BASE)
            conn.unbind()
            return True, "Connection successful"
        conn.unbind()
        return False, "Failed to bind"
    except ImportError:
        return False, "ldap3 library not installed (pip install ldap3)"
    except Exception as exc:
        logger.exception("LDAP test error: %s", exc)
        return False, str(exc)


async def authenticate_ldap_user(config: dict, username: str, password: str) -> dict | None:
    """Authenticate a user against LDAP via bind. Returns user attributes or None."""
    if not config.get("server_url") or not config.get("base_dn"):
        return None

    try:
        import ldap3

        server = ldap3.Server(config["server_url"], get_info=ldap3.ALL)
        attr_username = config.get("attr_username", "sAMAccountName")
        attr_email = config.get("attr_email", "mail")
        attr_display = config.get("attr_display_name", "displayName")
        base_dn = config["base_dn"]
        user_filter = config.get("user_filter", "(objectClass=person)")

        bind_dn = config.get("bind_dn", "")
        bind_password = config.get("bind_password", "")
        conn = ldap3.Connection(server, user=bind_dn, password=bind_password, auto_bind=True)

        search_filter = f"(&{user_filter}({attr_username}={ldap3.utils.conv.escape_filter_chars(username)}))"
        conn.search(base_dn, search_filter, attributes=[attr_username, attr_email, attr_display, "distinguishedName"])
        if len(conn.entries) == 0:
            conn.unbind()
            return None

        entry = conn.entries[0]
        user_dn = entry.distinguishedName.value if hasattr(entry, 'distinguishedName') else str(entry.entry_dn)
        email_val = getattr(entry, attr_email, None)
        email = str(email_val.value) if email_val and email_val.value else ""
        display_val = getattr(entry, attr_display, None)
        display_name = str(display_val.value) if display_val and display_val.value else ""
        conn.unbind()

        user_conn = ldap3.Connection(server, user=user_dn, password=password, auto_bind=True)
        user_conn.unbind()

        return {"username": username, "email": email, "display_name": display_name, "dn": user_dn}
    except ImportError:
        logger.error("ldap3 not installed")
        return None
    except Exception as exc:
        logger.warning("LDAP authentication failed for %s: %s", username, exc)
        return None


async def sync_ldap_users(config: dict) -> dict:
    try:
        import ldap3

        server_url = config.get("server_url")
        bind_dn = config.get("bind_dn")
        bind_password = config.get("bind_password")
        base_dn = config.get("base_dn")
        user_filter = config.get("user_filter", "(objectClass=person)")
        attr_username = config.get("attr_username", "sAMAccountName")
        attr_email = config.get("attr_email", "mail")
        attr_display = config.get("attr_display_name", "displayName")

        server = ldap3.Server(server_url, get_info=ldap3.ALL)
        conn = ldap3.Connection(
            server, user=bind_dn, password=bind_password, auto_bind=True
        )

        conn.search(
            base_dn, user_filter,
            attributes=[attr_username, attr_email, attr_display],
        )

        users_found = len(conn.entries)
        conn.unbind()

        return {"total_found": users_found, "created": 0, "updated": 0, "errors": 0}
    except ImportError:
        logger.error("ldap3 not installed")
        return {"total_found": 0, "created": 0, "updated": 0, "errors": 1}
    except Exception as exc:
        logger.exception("LDAP sync error: %s", exc)
        return {"total_found": 0, "created": 0, "updated": 0, "errors": 1}
