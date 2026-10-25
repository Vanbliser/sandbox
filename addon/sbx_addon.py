"""mitmproxy addon for a sandbox. It enforces the domain list and records headers and cookies.

Environment: SBX_DIR is the sandbox folder. It holds domains.txt, pinned.txt, access.log and sessions/.

Gates, in order:
  1. CONNECT: the name must be on the list. Otherwise the CONNECT gets a 403 and nothing else happens.
  2. Handshake: the SNI must be on the list and must equal the CONNECT name. A pinned host passes through
     untouched. A connection with no SNI is refused.
  3. Request (after decryption): the Host header must equal the SNI and be on the list. Otherwise 403.
Allowed requests are recorded: headers and cookies go to sessions/sessions.json, one block per domain.
"""

import json
import os
from datetime import datetime, timezone

from mitmproxy import http

SBX_DIR = os.environ["SBX_DIR"]
DOMAINS = os.path.join(SBX_DIR, "domains.txt")
PINNED = os.path.join(SBX_DIR, "pinned.txt")
ACCESS_LOG = os.path.join(SBX_DIR, "access.log")
SESSIONS_DIR = os.path.join(SBX_DIR, "sessions")
SESSIONS_PATH = os.path.join(SESSIONS_DIR, "sessions.json")


def stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_entries(path):
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            text = line.strip().lower()
            if text and not text.startswith("#"):
                entries.append(text)
    return entries


class Policy:
    """The list as written in domains.txt. 'all' allows every name. '.x' covers subdomains of x only."""

    def __init__(self):
        entries = read_entries(DOMAINS)
        self.all = "all" in entries
        self.exact = {e for e in entries if e != "all" and not e.startswith(".")}
        self.suffixes = tuple(e for e in entries if e.startswith("."))
        self.pinned = set(read_entries(PINNED))

    def listed(self, host):
        host = (host or "").lower().rstrip(".")
        if not host:
            return False
        if self.all:
            return True
        return host in self.exact or host.endswith(self.suffixes)


POLICY = Policy()
CONNECT_NAMES = {}  # client address -> CONNECT name, so the handshake can be compared with it
SESSIONS = {}


def log(decision, host, reason):
    with open(ACCESS_LOG, "a") as f:
        f.write(f"{stamp()} {decision} {host or '-'} {reason}\n")


def write_sessions():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    tmp = SESSIONS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(SESSIONS, f, indent=2, sort_keys=True)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, SESSIONS_PATH)


def block_for(host):
    return SESSIONS.setdefault(host, {"requests": 0, "headers": {}, "cookies": {}, "updated": None})


def deny(flow, host, reason):
    log("DENIED", host, reason)
    flow.response = http.Response.make(
        403,
        f"sandbox refused this request: {reason}\n",
        {"Content-Type": "text/plain", "X-Squid-Error": "ERR_ACCESS_DENIED"},
    )


def http_connect(flow):
    host = flow.request.pretty_host.lower()
    CONNECT_NAMES[flow.client_conn.address] = host
    if not POLICY.listed(host):
        deny(flow, host, "CONNECT name is not on the list")


def refuse_handshake(data, host, reason):
    # The handshake is refused before anything is decrypted. The connection is pointed at a closed local port,
    # so it fails and the client sees the connection close. No real host is contacted.
    log("DENIED", host, reason)
    data.context.server.address = ("127.0.0.1", 1)
    data.ignore_connection = True


def tls_clienthello(data):
    client = data.context.client
    sni = (client.sni or "").lower()
    connect_name = CONNECT_NAMES.get(client.address, "")
    if not sni:
        refuse_handshake(data, connect_name, "no SNI")
    elif connect_name != sni:
        refuse_handshake(data, sni, f"SNI {sni} does not match CONNECT name {connect_name or '-'}")
    elif not POLICY.listed(sni):
        refuse_handshake(data, sni, "SNI is not on the list")
    elif sni in POLICY.pinned:
        log("PASS-THROUGH", sni, "pinned: not decrypted")
        data.ignore_connection = True


def request(flow):
    sni = (flow.client_conn.sni or "").lower()
    host = (flow.request.headers.get("Host") or flow.request.pretty_host or "").split(":")[0].lower()
    if sni and host != sni:
        deny(flow, host, f"Host {host} does not match SNI {sni}")
        return
    if not POLICY.listed(host):
        deny(flow, host, "Host is not on the list")
        return
    block = block_for(host)
    block["requests"] += 1
    seen = stamp()
    for name, value in flow.request.headers.items():
        block["headers"][name] = value
        if name.lower() == "cookie":
            for pair in value.split(";"):
                key, sep, cookie = pair.partition("=")
                if sep:
                    block["cookies"][key.strip()] = {"value": cookie.strip(), "source": "request", "seen": seen}
    block["updated"] = seen
    log("ALLOWED", host, f"{flow.request.method} {flow.request.path}")
    write_sessions()


def response(flow):
    host = (flow.request.headers.get("Host") or flow.request.pretty_host or "").split(":")[0].lower()
    if not host or not POLICY.listed(host):
        return
    block = block_for(host)
    seen = stamp()
    for name, value in flow.response.headers.items():
        if name.lower() == "set-cookie" and "=" in value.split(";")[0]:
            key, _, cookie = value.split(";")[0].partition("=")
            block["cookies"][key.strip()] = {"value": cookie.strip(), "source": "set-cookie", "seen": seen}
    block["updated"] = seen
    write_sessions()
