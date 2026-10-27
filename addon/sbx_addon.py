"""mitmproxy addon for a sandbox or a group. It enforces the domain list and records headers and cookies.

Environment: SBX_DIR is the folder of the sandbox or group that owns this proxy. It holds domains.txt, pinned.txt
and access.log. A group's folder also holds members.json, which lists each member's address and sessions folder.

Gates, in order:
  1. CONNECT: the source must be a member (for a group), and the name must be on the list. Otherwise 403.
  2. Handshake: the SNI must be on the list and must equal the CONNECT name. A pinned host passes through
     untouched. A connection with no SNI is refused.
  3. Request (after decryption): the Host header must equal the SNI and be on the list. Otherwise 403.
Allowed requests are recorded: headers and cookies go to a sessions/sessions.json file. For a group, each member
has its own file, in the member's folder.
"""

import json
import os
from datetime import datetime, timezone

from mitmproxy import http

SBX_DIR = os.environ["SBX_DIR"]
DOMAINS = os.path.join(SBX_DIR, "domains.txt")
PINNED = os.path.join(SBX_DIR, "pinned.txt")
ACCESS_LOG = os.path.join(SBX_DIR, "access.log")
MEMBERS_PATH = os.path.join(SBX_DIR, "members.json")
OWN_SESSIONS = os.path.join(SBX_DIR, "sessions")


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


def mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return None


class Policy:
    """The list as written in domains.txt. 'all' allows every name. '.x' covers subdomains of x only.
    The files are read again whenever they change, so a list can be changed without restarting the proxy."""

    def __init__(self):
        self.stamps = None
        self.refresh()

    def refresh(self):
        stamps = (mtime(DOMAINS), mtime(PINNED))
        if stamps == self.stamps:
            return
        self.stamps = stamps
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


def load_members():
    """None for a sandbox with its own proxy. For a group, a dict from member address to member record."""
    if not os.path.exists(MEMBERS_PATH):
        return None
    with open(MEMBERS_PATH) as f:
        return {m["ip"]: m for m in json.load(f)}


POLICY = Policy()
MEMBERS = load_members()
MEMBERS_STAMP = mtime(MEMBERS_PATH)


def refresh():
    """Called for each request, so membership and list changes apply at once. Reads a file only if it changed."""
    global MEMBERS, MEMBERS_STAMP
    POLICY.refresh()
    if mtime(MEMBERS_PATH) != MEMBERS_STAMP:
        MEMBERS_STAMP = mtime(MEMBERS_PATH)
        MEMBERS = load_members()
CONNECT_NAMES = {}  # client address -> CONNECT name, so the handshake can be compared with it
SESSIONS = {}  # owner (member name, or "" for a sandbox) -> {host: block}


def log(decision, owner, host, reason):
    who = owner or "-"
    with open(ACCESS_LOG, "a") as f:
        f.write(f"{stamp()} {decision} {who} {host or '-'} {reason}\n")


def member_of(client):
    """(owner, sessions folder, ok). For a sandbox proxy the owner is '' and ok is always True."""
    if MEMBERS is None:
        return "", OWN_SESSIONS, True
    record = MEMBERS.get(client.address[0])
    if record is None:
        return "", "", False
    return record["name"], record["sessions"], True


def write_sessions(owner, folder):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "sessions.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(SESSIONS.get(owner, {}), f, indent=2, sort_keys=True)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def block_for(owner, host):
    blocks = SESSIONS.setdefault(owner, {})
    return blocks.setdefault(host, {"requests": 0, "headers": {}, "cookies": {}, "updated": None})


def deny(flow, owner, host, reason):
    log("DENIED", owner, host, reason)
    flow.response = http.Response.make(
        403,
        f"sandbox refused this request: {reason}\n",
        {"Content-Type": "text/plain", "X-Squid-Error": "ERR_ACCESS_DENIED"},
    )


def http_connect(flow):
    refresh()
    owner, _, ok = member_of(flow.client_conn)
    host = flow.request.pretty_host.lower()
    if not ok:
        log("DENIED", "", host, f"source {flow.client_conn.address[0]} is not a member of this group")
        flow.response = http.Response.make(403, "sandbox refused this request: not a member\n",
                                           {"X-Squid-Error": "ERR_ACCESS_DENIED"})
        return
    CONNECT_NAMES[flow.client_conn.address] = host
    if not POLICY.listed(host):
        deny(flow, owner, host, "CONNECT name is not on the list")


def refuse_handshake(data, owner, host, reason):
    # The handshake is refused before anything is decrypted. The connection is pointed at a closed local port,
    # so it fails and the client sees the connection close. No real host is contacted.
    log("DENIED", owner, host, reason)
    data.context.server.address = ("127.0.0.1", 1)
    data.ignore_connection = True


def tls_clienthello(data):
    refresh()
    client = data.context.client
    owner, _, ok = member_of(client)
    sni = (client.sni or "").lower()
    connect_name = CONNECT_NAMES.get(client.address, "")
    if not ok:
        refuse_handshake(data, "", sni or "-", "source is not a member of this group")
    elif not sni:
        refuse_handshake(data, owner, connect_name, "no SNI")
    elif connect_name != sni:
        refuse_handshake(data, owner, sni, f"SNI {sni} does not match CONNECT name {connect_name or '-'}")
    elif not POLICY.listed(sni):
        refuse_handshake(data, owner, sni, "SNI is not on the list")
    elif sni in POLICY.pinned:
        log("PASS-THROUGH", owner, sni, "pinned: not decrypted")
        data.ignore_connection = True


def request(flow):
    refresh()
    owner, folder, ok = member_of(flow.client_conn)
    sni = (flow.client_conn.sni or "").lower()
    host = (flow.request.headers.get("Host") or flow.request.pretty_host or "").split(":")[0].lower()
    if not ok:
        deny(flow, "", host, "source is not a member of this group")
        return
    if sni and host != sni:
        deny(flow, owner, host, f"Host {host} does not match SNI {sni}")
        return
    if not POLICY.listed(host):
        deny(flow, owner, host, "Host is not on the list")
        return
    block = block_for(owner, host)
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
    log("ALLOWED", owner, host, f"{flow.request.method} {flow.request.path}")
    write_sessions(owner, folder)


def response(flow):
    refresh()
    owner, folder, ok = member_of(flow.client_conn)
    host = (flow.request.headers.get("Host") or flow.request.pretty_host or "").split(":")[0].lower()
    if not ok or not host or not POLICY.listed(host):
        return
    block = block_for(owner, host)
    seen = stamp()
    for name, value in flow.response.headers.items():
        if name.lower() == "set-cookie" and "=" in value.split(";")[0]:
            key, _, cookie = value.split(";")[0].partition("=")
            block["cookies"][key.strip()] = {"value": cookie.strip(), "source": "set-cookie", "seen": seen}
    block["updated"] = seen
    write_sessions(owner, folder)
