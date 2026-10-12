# sandbox

Run AI agents, including pentesting agents, inside an Apple `container` microVM with a host-side network policy. Each sandbox is contained by a firewall on the host, and you choose what it may reach:

- **web mode:** web traffic only, to the domains you list, through a checking proxy
- **network mode:** network tools (`ping`, `nmap`, `traceroute`) only, to the addresses you list
- **both mode:** both of the above, with web traffic still checked by domain

Every sandbox logs what it tried, and you can cut its network or stop it at any time.

## Choosing a mode

| | web | network | both |
|---|---|---|---|
| Requires | `--domains` | `--ips` | `--domains` and `--ips` |
| Web (HTTP and HTTPS) | Allowed hosts only, through the proxy | Not allowed by name. Raw connections to listed addresses on any port, web ports included | Allowed hosts only, through the proxy |
| `ping`, `traceroute` | Blocked | Allowed to listed addresses | Allowed to listed addresses |
| `nmap` on non-web ports | Blocked | Allowed to listed addresses | Allowed to listed addresses |
| `nmap` on ports 80 and 443 | Blocked | Allowed to listed addresses | Blocked, so web scans don't reach targets |
| Logging | Every web request (proxy log), and every pf decision (network log) | Every pf decision (network log) | Both logs |
| Domain check on web | Yes | No | Yes |
| `--strict` | Yes | Not applicable | Yes |
| DNS for the sandbox | Blocked | Blocked | Blocked |
| Host protection | Yes | Yes | Yes |

When you start a sandbox, `sandbox up` prints a short note describing the path you chose.

## What's enforced

| Control | How |
|---|---|
| Isolation | Each sandbox is an Apple `container` microVM with its own kernel and network |
| Default deny | pf blocks all traffic from the sandbox, except the paths the mode allows |
| Proxy path | Web requests go to Squid, which checks each one against `domains.txt` |
| Host protection | The sandbox can't reach the host's services, including SSH |
| DNS | Blocked for the sandbox. Names are resolved on the host, by the proxy |
| Visibility | Web decisions are logged by the proxy (`access.log`). Network decisions are logged by pf, one record per packet, grouped per flow by `sandbox logs` |
| Kill switch | `down` cuts the network and stops the container. `destroy` removes everything |
| Strict mode | Decrypts HTTPS and requires the TLS name and the Host header to match |

## What's not covered

Read these before relying on a sandbox:

- **Web traffic to addresses in network mode, and to the addresses in both mode on non-web ports, is not checked by name.** Those connections go straight to the address, so the proxy never sees them.
- **nmap can't scan web ports through the proxy.** In both mode, web ports on listed addresses are closed to raw traffic on purpose. Web testing uses the domains list.
- **Other HTTP services bypass the check.** A web app on port 8080 or 8000 is not a web port in the rules, so it goes raw in network mode and in the non-web part of both mode. The rules can't see what protocol runs on a port.
- **Shared-hosting addresses serve other sites.** A listed address on a CDN can serve sites you never approved, and raw traffic reaches them too.
- **Pinned clients.** Trust and pinning are separate. Pinning means the client accepts only one specific certificate, even one from a trusted authority. Strict mode replaces the target's certificate, so a pinned client fails there. Default mode passes HTTPS through untouched, so pinned clients work. Direct-IP connections are always decrypted, so pinned clients can fail on those. There's no per-host exemption from strict mode yet.
- **Trust follows the Mac.** `init` copies the trust roots from this Mac's keychains into the image. If your network inspects HTTPS, your company's root is included, so the sandbox trusts what your Mac trusts.
- **Credentials.** Logins and API keys live inside the sandbox. An agent that reads them can send them to any reachable host. Use test accounts, and keep the lists tight.
- **Port restrictions on the proxy.** The domain list matches hostnames, not ports.
- **Anything inside an allowed host.** Allowed sites are reachable in full. Scope is still the operator's job.

## Requirements

- macOS on Apple silicon, with Apple's `container` CLI from <https://github.com/apple/container/releases>
- Homebrew (<https://brew.sh>). `init` installs `squid` through it, after asking
- The Xcode Command Line Tools, for `python3` (`xcode-select --install`)
- An administrator account. The tool needs root for pf rules, so the installer and the `up`, `down` and `destroy` steps need `sudo`. Without it, `up` fails, and `verify` reports the pf rules as missing

## Install

```bash
git clone <repo-url> sandbox
cd sandbox
sudo ./install.sh                # one-time: installs the pf helper, its sudo rule and the 'sandbox' command
sandbox init                     # checks dependencies, creates your CA, builds the image
```

`sudo ./install.sh` asks for your password once. It also asks, before it changes anything, whether to add `~/.local/bin` to your PATH.

`init` creates the CA at `~/.sandbox/ca/`. The private key stays on your machine, with owner-only permissions. Each teammate runs `init` on their own Mac, so nobody shares a key.

To remove everything the installer put in place: `sudo ./install.sh --uninstall`. This leaves your `~/.sandbox` state alone.

### What the installer's root part allows

pf rules need root. Rather than give the tool full sudo, `sudo ./install.sh` installs:

- `/usr/local/libexec/sandbox-pf`, a root-owned helper. It accepts only `load`, `show`, `flush`, `capture-start` and `capture-stop`, and only for a `com.apple/sandbox-*` anchor. It builds the rules itself from validated values, and it never accepts rule text.
- `/etc/sudoers.d/sandbox`, which lets your user run `load`, `show`, `capture-start` and `capture-stop` without a password.

`flush` isn't in that rule. Taking down a sandbox's containment, through `down` or `destroy`, asks for your password.

Run `sudo ./install.sh` again after updating the tool, so the helper matches the script.

## Quick start

1. **Web mode**: a domains file. The defaults (package registries, AI APIs, OS mirrors) are included unless you pass `--no-defaults`.

   ```bash
   cp examples/engagement.txt my-domains.txt      # edit to your targets
   sandbox up app1 --mode web --domains my-domains.txt
   ```

2. **Network mode**: an IP file. Single addresses, ranges and CIDR blocks are all accepted.

   ```bash
   cp examples/ips.txt my-ips.txt                 # edit to your targets
   sandbox up scan1 --mode network --ips my-ips.txt
   ```

3. **Both modes**:

   ```bash
   sandbox up app1 --mode both --domains my-domains.txt --ips my-ips.txt
   ```

4. **Verify the sandbox before every run:**

   ```bash
   sandbox verify app1
   ```

   Every line should say PASS. Don't run an agent against a target until it does.

   Run it every time, not only the first time. The sandbox's containment depends on things outside the tool: the Mac's routes, its VPN, its NAT and firewall state, and the container service. Any of them can change between runs. Connecting or disconnecting the corporate VPN is a common example. A sandbox that passed `verify` yesterday can fail today, and `verify` is the check that catches it before the agent does. `up` tests the network only at the moment it starts, so `verify` is the check for everything after that.

5. Run the agent inside the sandbox:

   ```bash
   container exec -it sbx-app1 sh
   ```

6. In another terminal, watch what the sandbox tries. Web decisions come from the proxy, and network decisions from pf (network and both modes):

   ```bash
   sandbox logs app1 -f
   ```

7. When finished:

   ```bash
   sandbox down app1       # stops the container, proxy and rules; keeps the state
   sandbox destroy app1    # removes everything and archives the logs and policy
   ```

## Commands

The `container` service is started automatically when a command needs it, so you don't need to run `container system start` yourself. If it fails to start, the message says so.


| Command | What it does |
|---|---|
| `sandbox init` | Check dependencies, create this Mac's CA, build the image |
| `sandbox up <name> --mode web\|network\|both [...]` | Create a sandbox in the chosen mode |
| `sandbox ips <name> <file>` | Replace the IP list and reload the rules (network and both) |
| `sandbox strict <name> on\|off` | Switch strict Host/SNI checking (web and both) |
| `sandbox verify <name>` | Run the containment checks, PASS or FAIL for each |
| `sandbox list` | List every sandbox on this Mac: mode, container state, subnet, proxy and capture |
| `sandbox status <name>` | Show mode, container, proxy, rules and policy size |
| `sandbox logs <name> [-f]` | Show web decisions (proxy) and network decisions (pf), or follow them |
| `sandbox down <name>` | Stop the container, proxy and rules. Keeps the state |
| `sandbox destroy <name>` | Remove the sandbox. Archives logs and policy under `~/.sandbox/archive/`. Reports anything it couldn't remove (container, network, proxy, pf rules, capture) |

## Input files

**Domains file** (web and both): one hostname per line. A leading dot covers the domain and all its subdomains. Lines starting with `#` are ignored.

```
# my-domains.txt
app.example.com
.example.com          # example.com and every subdomain
```

**IP file** (network and both): one entry per line, in any of three forms.

```
# my-ips.txt
203.0.113.10                 # a single address
203.0.113.20-203.0.113.40    # a range
198.51.100.0/24              # a CIDR block
```

`up` and `ips` reject malformed entries, anything broader than a /8, anything overlapping the sandbox's own subnet, and any address belonging to this Mac. Ranges are converted to CIDR blocks.

**Defaults** (`defaults.txt`, web and both): the Balanced list from Docker Sandboxes, minus any host that lets people upload or host their own content. Package registries are included.

## Strict mode

By default, web traffic is passed through without decryption, so the proxy checks only the hostname in the connection request. Strict mode decrypts each connection and requires the TLS name to match the Host header. A request can't reach a site it wasn't approved for, even on a shared host.

```bash
sandbox up app1 --mode web --domains my-domains.txt --strict   # start in strict mode
sandbox strict app1 on                                          # switch on a running sandbox
sandbox strict app1 off                                         # switch back
```

Changes apply to new connections.

## The components

Each piece below does one job. Here's what each one is and what the script does with it.

**Apple `container` CLI.** Runs the sandbox as a lightweight virtual machine with its own kernel. The script creates a network per sandbox with `container network create`, starts the sandbox with `container run`, reads its gateway address with `container inspect`, and runs commands in it with `container exec`. Installing it is manual, from Apple's releases page.

**pf (macOS packet filter).** The firewall in the macOS kernel. It decides which packets leave the sandbox's bridge. Every rule the script writes lives in its own anchor, `com.apple/sandbox-<name>`, so it never touches your system rules or your own traffic. The rules are default-deny, with exceptions for the proxy port, the raw-traffic addresses, and nothing else. pf also blocks the sandbox from the host's own services.

**The bridge.** A virtual network interface on the host, one per sandbox. The sandbox's traffic enters the host through it, which is where pf filters it. The script finds the bridge by looking up the gateway address, not by name, because macOS reuses bridge names.

**`pflog0` and the network log.** `pflog0` is macOS's interface for pf's log records. The helper creates it when it's missing. Each pf rule that matters has the `log` keyword, so every allow and block is recorded. A root-owned `tcpdump` reads `pflog0` and writes to the sandbox's `pflog.txt`. `sandbox logs` groups those records into one line per flow, with the verdict, the protocol, the addresses and a count.

**Squid.** The web proxy, installed with Homebrew. The script generates one configuration per sandbox and runs one Squid process for each. It listens on the sandbox's gateway address, on port 3128, and checks each connection against `domains.txt`. Connections to listed hosts pass through unchanged. In strict mode, Squid decrypts them and checks the Host header. Squid also writes `access.log`.

**The sandbox CA.** A certificate authority created on your Mac by `init`. Squid uses it to present certificates in strict mode, and the sandbox image trusts it. The private key never leaves `~/.sandbox/ca/`.

**The image.** A Linux image built by `init` with `container build`. It contains curl, Node, npm and nmap. It trusts the sandbox CA and the roots exported from your Mac's keychains. The keychain roots are how a sandbox trusts the same sites your Mac does.

**The privileged helper** (`/usr/local/libexec/sandbox-pf`). The only root-owned part of the tool. The script calls it through sudo to load, show and flush the pf anchor. It validates every argument: the sandbox name, the subnet, the mode and each address. It builds the rules itself and never accepts rule text. It can only change `com.apple/sandbox-*` anchors.

**The sudo rule** (`/etc/sudoers.d/sandbox`). Lets your user run the helper's `load` and `show` without a password. `flush` still asks for your password.

**The Host-check helper** (`~/.sandbox/bin/check-host.sh`). Squid calls it for each request in strict mode. It compares the Host header and TLS name against `domains.txt`, and in default mode it checks the destination address against the names' DNS answers.

**dig, python3 and the system tools.** `dig` resolves names for the Host-check helper. `python3` converts IP ranges to CIDR blocks. `ifconfig`, `netstat` and `pgrep` find free subnets, bridges and proxy processes. `security` exports the keychain roots. `nc` and `curl` run the containment checks.

**`~/.sandbox/`** holds the state of each sandbox. Per sandbox: `state.json`, the policy files, `squid.conf`, `access.log`, and the rules' input files.

## What `up` does, in order

1. Checks the mode and the input files.
2. Refuses to start if a leftover network or container with the same name exists.
3. Tries each free address range in `192.168.200.0/24` to `192.168.250.0/24`. For each one it creates the network, then runs a short outbound test from a container, before any firewall rules exist. A range that fails the test is removed and the next one is tried. If none pass, `up` stops and says so. This catches networks that are broken on the Mac, including leftover NAT rules.
4. Starts the container, reads its gateway address, and finds its bridge.
5. Writes the mode and the policy: `domains.txt` for web and both, and `ips.txt` for network and both, after checking every entry.
6. In web and both modes, writes Squid's configuration and starts Squid on the gateway address.
7. Loads the pf rules through the privileged helper.
8. In network and both modes, starts the network log: a root-owned `tcpdump` on `pflog0`, writing to the sandbox's `pflog.txt`.
9. Prints the mode note.

If any step fails, the rollback stops Squid, flushes the pf rules, removes the container and network, and moves the logs to `~/.sandbox/failed/`.

## Files

```
~/.sandbox/
  ca/                       your CA (ca.key is private)
  bin/check-host.sh         Host-check helper for Squid
  image/                    build context for the sandbox image
  <name>/
    access-mode             web, network or both
    policy                  default or strict (web and both)
    domains.txt             the allowlist (web and both)
    ips.txt                 normalised CIDR blocks (network and both)
    state.json              subnet, gateway, bridge, container name
    squid.conf              generated proxy configuration
    access.log              every proxy decision (web and both)
    pflog.txt               every pf decision, one record per packet (network and both)
    capture.pid             process ID of the network log capture
  archive/                  destroyed sandboxes
  failed/                   logs from sandboxes that failed to start
```

## Troubleshooting

- **HTTPS fails inside the sandbox with a certificate error:** run `sandbox init` again. It re-exports the Mac's trust roots into the image.
- **`up` says the port is in use:** another process holds port 3128 on the gateway. `lsof -nP -iTCP:3128 -sTCP:LISTEN` shows which one. `sandbox` won't start on a port it doesn't own.
- **`up` refuses an IP entry:** the message names the entry and the reason: malformed, too broad, overlapping the sandbox subnet, or belonging to this Mac.
- **`up` or `destroy` says a network won't delete, or reports a leftover network:** the container service has a stale record for it, which `sandbox` can't remove. Reboot your Mac, then check with `container network list`. The message names the network. `sandbox` doesn't edit the service's internal records, so a reboot is the supported fix.
- **`up` failed:** the rollback removed what it created. Check `squid.out` and `cache.log` in `~/.sandbox/failed/`.
- **A pf rule change didn't take effect:** the helper may be out of date. Run `sudo ./install.sh` again.
- **Stray proxy processes:** `pgrep -fl squid` lists them. `sandbox destroy` stops the proxy for its sandbox by its configuration path.

## For developers

The tool is one Bash script, `sandbox`, with a privileged helper, `privileged/sandbox-pf`. There's no daemon. Each command runs to completion and keeps its state in `~/.sandbox/<name>/`.

- Check syntax after every change: `bash -n sandbox && bash -n privileged/sandbox-pf`
- Test each change end to end: `up`, `verify`, the traffic checks, then `destroy`. There are no automated tests yet.
- After changing the helper, reinstall it with `sudo ./install.sh`.
- Don't commit anything under `~/.sandbox`. `.gitignore` covers keys and state.

Open items:
- A way to exempt a pinned host from strict mode
- Filtering the host's own multicast from the network log
- Port restrictions on the proxy
- Burp as an optional upstream proxy
- Automated tests
