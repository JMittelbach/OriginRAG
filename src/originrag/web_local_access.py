from ipaddress import ip_address
from urllib.parse import urlsplit


def local_browser_request(server_address, client_address, headers) -> bool:
    try:
        if not ip_address(server_address[0]).is_loopback or not ip_address(client_address[0]).is_loopback:
            return False
        hosts = headers.get_all("Host") or []
        if len(hosts) != 1:
            return False
        host = hosts[0]
        if any(character.isspace() for character in host) or len(headers.get_all("Origin") or []) > 1:
            return False
        parsed = urlsplit("http://" + host)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password:
            return False
        if parsed.path or parsed.query or parsed.fragment or (parsed.port or 80) != server_address[1]:
            return False
        if headers.get("Sec-Fetch-Site", "") not in {"", "same-origin", "none"}:
            return False
        origin = headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            return False
        return True
    except (ValueError, TypeError, IndexError):
        return False
