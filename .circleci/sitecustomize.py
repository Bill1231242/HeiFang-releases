"""CI-only Python transport guard; inherited by Python child processes."""
import ipaddress
import os
import sys


def allow_host(host):
    if host is None or host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or (
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped is not None
            and address.ipv4_mapped.is_loopback
        )
    except ValueError:
        return False


def guard(event, args):
    host = None
    if event in {"socket.connect", "socket.sendto"} and isinstance(args[1], tuple):
        host = args[1][0]
    elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        host = args[0]
    else:
        return
    if not allow_host(host):
        # Do not log URLs, headers, credentials, or supplier response bodies.
        raise OSError("CI verification blocks non-loopback network access")


if os.environ.get("HEIFANG_VERIFY_OFFLINE") == "1":
    sys.addaudithook(guard)
