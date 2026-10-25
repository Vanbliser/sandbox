# sandbox

The sandbox tool is specifically built to run AI pentesting agents in isolated environments on your Mac machine. It is built with the Apple `container` microVM and a host-side firewall.

Every sandbox logs what it tried, and you can cut its network or stop it at any time.


## The components

**Apple `container` CLI.** Runs the sandbox as a lightweight virtual machine with its own kernel. The script creates a network per sandbox, starts the sandbox, reads its gateway address, and runs commands in it.

**pf (macOS packet filter).** The firewall in the macOS kernel. It decides which packets leave the sandbox's bridge. Every rule the script writes lives in its own anchor, `com.apple/sandbox-<name>`, so it never touches your system rules or your own traffic.

**The bridge.** A virtual network interface on the host, one per sandbox. The sandbox's traffic enters the host through it, which is where pf filters it. The script finds the bridge by the gateway address, because macOS reuses bridge names.

**`pflog0` and the network log.** `pflog0` is macOS's interface for pf's log records. The helper creates it when it's missing. Each rule that matters has the `log` keyword, so every allow and block is recorded. A root-owned `tcpdump` reads them and writes the sandbox's `pflog.txt`. `sandbox logs` groups them into one line per flow.

**mitmproxy.** The web proxy. One process per sandbox on the sandbox's gateway. Setup installs it with Homebrew, and the sandbox's own CA signs the certificates.

**The sandbox CA.** A certificate authority created on your Mac by `setup`. The proxy uses it to sign the certificates it presents, and the sandbox image trusts it. The private key stays in `~/.sandbox/ca/`. Each sandbox's proxy also keeps a copy in its `mitm/` folder, which `destroy` deletes.

**The image.** An Ubuntu 24.04, with Playwright 1.59.1 and its Chromium. Setup downloads these on the Mac into `~/.sandbox/image/pw`, so the image build doesn't depend on npm. The image contains curl, Node, npm and nmap. It trusts the sandbox CA and the roots exported from your Mac's keychains, which is how a sandbox trusts the same sites your Mac does.

**The privileged helper** (`/usr/local/libexec/sandbox-pf`). The only root-owned part of the tool. The script calls it through sudo to load, show and flush the pf anchor, and to start and stop the network log. It validates every argument, including the paths of log files. It builds the rules itself and never accepts rule text.

**The sudo rule** (`/etc/sudoers.d/sandbox`). Lets your user run the helper's `load`, `show`, `capture-start` and `capture-stop` without a password. `flush` still asks for your password.

**Standard tools.** `python3` converts IP ranges to CIDR blocks. `ifconfig`, `netstat` and `pgrep` find free subnets, bridges and proxy processes. `security` exports the keychain roots. `nc` and `curl` run the containment checks. Setup checks for `dig`.

## Requirements

- macOS on Apple silicon, with Apple's `container` CLI from <https://github.com/apple/container/releases>
- Homebrew (<https://brew.sh>). Setup installs `mitmproxy` through it, after asking
- The Xcode Command Line Tools, for `python3` (`xcode-select --install`)
- An administrator account. The tool needs root for pf rules, so the installer needs `sudo`. Without it, `up` fails, and `verify` reports the pf rules as missing

## Install

Installation happens once per Mac.

```bash
git clone https://github.com/Vanbliser/sandbox sandbox
cd sandbox
sudo ./install.sh     # installs the firewall helper, the command, and the Mac-level setup
```

`sudo ./install.sh` asks for your password once. It sets up the CA and the image as your user, so the files are yours. It asks before it changes your shell profile. It detects your login shell and writes the PATH line to the matching file: `~/.zshrc` for zsh, `~/.bash_profile` for bash, `~/.config/fish/config.fish` for fish, and `~/.profile` for anything else. When it has changed a file, it prints the `source` command for that shell.

The CA's private key stays in `~/.sandbox/ca/`, with owner-only permissions. The proxy keeps a copy in each sandbox's `mitm/` folder, which `destroy` deletes.

To remove everything the installer put in place: `sudo ./install.sh --uninstall`. This leaves `~/.sandbox` and your projects alone.

### What the installer's root part allows

pf rules need root. Rather than give the tool full sudo, the installer adds:

- `/usr/local/libexec/sandbox-pf`, a root-owned helper. It accepts only `load`, `show`, `flush`, `capture-start` and `capture-stop`, and only for a `com.apple/sandbox-*` anchor. It builds the rules itself from validated values, and it never accepts rule text.
- `/etc/sudoers.d/sandbox`, which lets your user run `load`, `show`, `capture-start` and `capture-stop` without a password.

`flush` isn't in that rule, so it needs your password. `down` and `destroy` run it and hide any error. If the rules are still there afterwards, `destroy` reports them. Clear them with `sudo /usr/local/libexec/sandbox-pf flush <project>-<name>`.


## Update the tool

Do this after you pull a new version of the tool:

```bash
cd sandbox
git pull
sudo ./install.sh     # reinstalls the firewall helper and the sudo rule, then runs setup
sandbox verify <name> # for each sandbox you use
```

`sudo ./install.sh` runs `setup` for you. Setup copies the proxy addon to `~/.sandbox/bin/`, refreshes the trust roots, and rebuilds the image if its files changed.

Sandboxes that already exist keep the settings they started with, so restart them to pick up changes:

- **Proxy or addon changes:** `sandbox down <name>`, then `sandbox start <name>`.
- **Firewall rule changes:** `sandbox start <name>` reloads the rules. You don't need to recreate the sandbox.
- **Image changes** (new packages in the image): recreate the sandbox with `sandbox destroy <name>`, then `sandbox up <name>`. `destroy` may leave pf rules behind, so flush them before you recreate the sandbox:
  `sudo /usr/local/libexec/sandbox-pf flush <project>-<name>`

If the helper is out of date, `up` and `start` fail, or the firewall rules don't match the script. Running `sudo ./install.sh` fixes that.

## Commands

| Command | What it does |
|---|---|
| `sandbox setup` | Once per Mac: create the CA and build the image. The installer runs it for you |
| `sandbox init` | In a folder: make it a sandbox project |
| `sandbox up <name> [-m web\|network\|both] [-d <file>] [-i <file>] [-p <file>] [--upstream <host:port>] [--upstream-ca <pem\|der>] [--ui] [--no-defaults] [-e NAME=VALUE \| --env-file <file>]` | Create a sandbox. Mode defaults to `both`. Missing lists are created empty |
| `sandbox domain <name> <file>` | Replace the domain list and reload the proxy (web and both) |
| `sandbox ip <name> <file>` | Replace the IP list and reload the firewall (network and both) |
| `sandbox pin <name> <file>\|none` | Replace the pinned list on a running sandbox and reloads the proxy. `none` clears the list. |
| `sandbox status <name>` | Show mode, container, proxy and rules |
| `sandbox verify <name>` | Run the containment checks, PASS or FAIL for each |
| `sandbox shell <name>` | Open a shell inside the sandbox |
| `sandbox ui <name>` | Open the sandbox interface in a browser (needs `up --ui`) |
| `sandbox logs <name> [-f]` | Web decisions (proxy) and network decisions (pf), or follow them live. It shows only what the sandbox sent or tried to send |
| `sandbox down <name>` | Stop the container, proxy and rules. Keeps the state |
| `sandbox upstream <name> [<host:port> <ca_file> \| none]` | Sends web traffic through an upstream proxy, such as Burp. The CA file may be PEM or DER. `none` removes it |
| `sandbox start <name>` | brings back a sandbox that sandbox down has stopped. reusing the kept state |
| `sandbox destroy <name>` | Remove the sandbox. Archives its logs and settings in the project's `.sandbox/archive/` |
| `sandbox list` | List every sandbox in every project on this Mac |

Sandbox names are lowercase letters, digits and hyphens. The project name and the sandbox name together must be 50 characters or fewer.


## How the workflow fits together

There are three levels:

1. **Your Mac, once.** `sudo ./install.sh` installs the firewall helper, the `sandbox` command, and the Mac-level setup (the CA and the sandbox image).
2. **A project folder.** Any folder you work in can become a sandbox project. `sandbox init` makes it one. The project's settings, lists and logs live in its `.sandbox/` folder.
3. **Sandboxes in a project.** A project can have as many sandboxes as you need. Each one is named after the project, for example `webapp-app1`. Sandbox names are <project>-<sandbox>, and that's also the directory name under .sandbox/sandboxes/. Containers, networks and pf anchors use the same name, so projects can't collide.

```
~/your-projects/webapp/          <- a sandbox project
  .sandbox/
    project.json                 project name (committed)
    .gitignore                   what to commit (committed)
    sandboxes/
      webapp-app1/               one sandbox
        engagement.txt           your domains (committed)
        ips-source.txt           your IP list (committed)
        pinned.txt, upstream, upstream-ca.pem, ui, access-mode, defaults   settings (committed)
        domains.txt, ips.txt     generated (ignored)
        state.json, logs, ...    runtime (ignored)
        browser/, sessions/      browser data and captured sessions (ignored)
        mitm/, env               proxy CA copy and environment values (ignored)
    archive/                     destroyed sandboxes (ignored)
    failed/                      sandboxes that failed to start (ignored)
```

## Start a project

In any folder you work in:

```bash
cd ~/your-projects/webapp
sandbox init
```

`init` makes the folder a sandbox project. It names the project after the folder (`webapp`), and creates `.sandbox/` with a `.gitignore` for it. If the folder is already a project, `init` says so and does nothing. If it's inside a project, it refuses.

Commit the project to git if you want to share its settings and lists. The `.gitignore` in `.sandbox/` already leaves out the runtime files.

## Create and use sandboxes

Run these from anywhere inside the project folder. The project is found from the current folder.

### Create a sandbox

Define your targets first, in one or both files. Then pass them to `up`:

```bash
sandbox up app1 -d my-domains.txt -i my-ips.txt
```

Each sandbox has two lists you control:

- **`domains.txt`**: which websites the sandbox may reach, through a checking proxy
- **`ips.txt`**: which addresses it may reach with network tools (`ping`, `traceroute`, `nmap`) that make direct connections

Or leave out the files and load them afterwards. Missing lists are created empty inside the sandbox's folder, and you load your own files afterwards:

```bash
sandbox up app1
sandbox domain app1 my-domains.txt
sandbox ip app1 my-ips.txt
```

The sandbox is named `webapp-app1`: the project name, a hyphen, then the name you chose. The container, network and firewall rules use the same full name, so sandboxes in different projects can't clash.

`-d` and `-i` are optional. A missing file means an empty list, which allows nothing for that kind of traffic. Pass `-m web` or `-m network` to use only one kind of list. If you pass a file that the mode doesn't use, `up` refuses it.

`up` checks every file before it creates anything. If a line is wrong, it stops and names the file and the line. Nothing is left behind.

### Use a sandbox from a clone

If someone else commits a project, its sandbox folders come with their lists. Running `sandbox up app1` in the cloned project builds the sandbox from those committed lists. You don't need to pass the files again.

### Verify before every run

```bash
sandbox verify app1
```

Every line should say PASS. Don't run an agent against a target until it does.

Run it every time, not only the first time. The containment depends on things outside the tool: the Mac's routes, its VPN, its NAT and firewall state, and the container service. Any of them can change between runs. A sandbox that passed `verify` yesterday can fail today.

### Work inside it

```bash
sandbox shell app1          # a shell inside the sandbox
sandbox logs app1 -f        # follow web and network decisions, live
```

### Change the lists later

```bash
sandbox domain app1 new-domains.txt   # replaces the domain list and reloads the proxy
sandbox ip app1 new-ips.txt           # replaces the IP list and reloads the firewall
```

Each command refuses a list the sandbox's mode doesn't use.

### Finish

```bash
sandbox down app1       # stops the container, proxy and rules; keeps the state
sandbox destroy app1    # removes the sandbox and archives its logs and settings in the project
```

### See everything on this Mac

```bash
sandbox list            # every sandbox in every project, with its project name
```

## Input files

**Domains file** (web and both): one hostname per line. A leading dot covers the domain and all its subdomains. `all` allows every hostname. Lines starting with `#` are ignored. An IP address in this file is refused, with a message that points you to the IP file.

```
# my-domains.txt
app.example.com
.example.com          # example.com and every subdomain
```

**IP file** (network and both): one entry per line, in any of three forms. `all` allows every address.

```
# my-ips.txt
203.0.113.10                 # a single address
203.0.113.20-203.0.113.40    # a range
198.51.100.0/24              # a CIDR block
```

`sandbox up`, `sandbox domain` and `sandbox ip` reject malformed entries, anything broader than a /8 other than `all`, anything overlapping the sandbox's own subnet, and any address belonging to this Mac. Ranges are converted to CIDR blocks.

**Defaults** (`defaults.txt`): a starting list of package registries, AI provider APIs and OS mirrors, based on the Balanced list from Docker Sandboxes, minus any host that lets people upload or host their own content. It's added to the domains list unless you pass `--no-defaults`.

## Choosing a mode

`sandbox up` takes a mode with `-m`. The default is `both`.

| | web | network | both (default) |
|---|---|---|---|
| Uses | `domains.txt` | `ips.txt` | both files |
| Web (HTTP and HTTPS) | Listed hosts, through the proxy | Raw, to listed addresses, web ports included | Listed hosts, through the proxy |
| `ping`, `traceroute`, non-web TCP/UDP | Blocked | To listed addresses | To listed addresses |
| `nmap` on web ports (80, 443) | Blocked | To listed addresses | Blocked, unless both lists are `all` |
| Logging | Proxy log | pf network log | Both logs |

## How the two lists combine

Both lists are optional. An empty list allows nothing for that kind of traffic.

| Domains | IPs | What the sandbox can reach |
|---|---|---|
| listed names | listed addresses | Listed websites through the proxy. Ping and non-web TCP/UDP to listed addresses. Web ports on those addresses stay closed to raw traffic. |
| listed names | `all` | As above, but ping and non-web TCP/UDP to every address. |
| `all` | `all` | Any website through the proxy. Any address on any port, including web ports. Web traffic to any address bypasses the proxy and the domain check. |
| empty | empty | Nothing. |

Two things are always blocked, whatever the lists say: DNS (port 53), and this Mac's own addresses.

The `all` keyword works in both files. It means "every hostname" in `domains.txt` and "every address" in `ips.txt`. Use it deliberately. `sandbox up` prints a warning whenever `all` is set, and says what it opens.


## Pinned hosts

Pinned hosts pass through without decryption. They are not checked and not recorded. Each pinned host must also be on the domain list.

```
sandbox up <name> -d domains.txt -p pinned.txt
sandbox pin <name> <file>
sandbox pin <name> none
```

- `sandbox up -p <file>` sets the pinned list when the sandbox is created.
- `sandbox pin <name> <file>` replaces the list on a running sandbox and reloads the proxy.
- `sandbox pin <name> none` clears the list.

**Rules:**
- Each pinned host must already be in the domain list. A host that isn't allowed is refused, and nothing changes.
- `all` can't be pinned. List the hosts you need.
- If `domain` removes a pinned host from the list, that host is dropped from `pinned.txt` and the command says so.
- `status` shows the pinned count. `pinned.txt` is a committed list, so it travels with the project like `engagement.txt`.

**Example files:**

`domains.txt`
```
example.com
.cdn.example.net
api.example.com
```

`pinned.txt`
```
example.com
api.example.com
```

## Environment variables

`up -e NAME=VALUE` (repeatable) and `up --env-file <file>`. In web modes the sandbox gets HTTP_PROXY, HTTPS_PROXY, and NO_PROXY automatically. Values are stored in the sandbox folder, owner-only, and never printed.


## What `sandbox up` does, in order

1. Checks the mode, and checks each list file, before anything is created. A bad file stops the command, names the file and the line, and leaves nothing behind.
2. Refuses to start if the sandbox is already running, or if a leftover network or container with its name exists.
3. Tries each free range in `192.168.200.0/24` to `192.168.250.0/24`. For each one it creates the network, then runs a short outbound test from a container, before any firewall rules exist. A range that fails is removed and the next one is tried. If none pass, `up` stops and says so.
4. Starts the container, reads its gateway address, and finds its bridge.
5. Writes the mode, the lists and the settings into the sandbox's folder in the project. Missing lists are written empty.
6. In web and both modes, writes the proxy's settings and starts mitmproxy on the gateway address.
7. Loads the pf rules through the privileged helper.
8. In network and both modes, starts the network log.
9. Prints the mode note and any warnings.

If any step fails, the rollback stops the proxy and the network log, flushes the pf rules, removes the container and network, and moves the sandbox's folder to the project's `.sandbox/failed/` (or keeps the lists, if they came from a clone).


## Upstream proxy

**What it does:** When set, mitmproxy sends web traffic to an upstream proxy, such as Burp, rather than straight out. The upstream's CA is trusted for that leg. The CA may be PEM or DER.

**Commands**
```
sandbox up <name> -d domains.txt --upstream 127.0.0.1:8080 --upstream-ca ~/Downloads/cacert.der
sandbox upstream <name> 127.0.0.1:8080 ~/Downloads/cacert.der
sandbox upstream <name> none
```

- `--upstream-ca` adds the upstream's CA to the trust list for the upstream leg. Without it, the upstream's certificates must be trusted by this Mac.
- `status` shows the upstream and whether a CA is trusted. `up` and `upstream` print the same.
- Upstream settings are committed with the project.

**Headers and cookies** are recorded per domain in sessions/sessions.json. This happens for every allowed request, with no flag needed. The folder is mounted read-only in the sandbox at /root/sessions. The file holds credentials in plain text, so keep it out of git.

**Browser folder and interface**

- browser/ is mounted read-write at /root/browser. Any tool in the sandbox can store its data there. It survives down and start, and destroy archives it.
- up --ui starts a web interface on 127.0.0.1:6080 (no password). ui <name> opens it. Its first pane is the sandbox desktop, where Chromium runs.

## What's enforced

| Control | How |
|---|---|
| Isolation | Each sandbox is an Apple `container` microVM with its own kernel and network |
| Default deny | pf blocks all traffic from the sandbox, except what its lists allow |
| Proxy path | Web requests go to mitmproxy, which checks each request against `domains.txt` |
| Host protection | The sandbox can't reach this Mac's services, including SSH |
| DNS | Blocked for the sandbox. Names are resolved on the host, by the proxy |
| Visibility | Web decisions are logged by the proxy (`access.log`). Network decisions are logged by pf (`pflog.txt`), and `sandbox logs` groups them by flow |
| Kill switch | `down` cuts the network and stops the container. `destroy` removes everything |
| Host and SNI | Refused at CONNECT if the name isn't listed. Refused at the handshake if the SNI isn't listed or doesn't match the CONNECT name. Refused per request if the Host doesn't match the SNI or isn't listed |

## What's not covered

Read these before relying on a sandbox:

- **Raw traffic isn't checked by name.** Anything sent to a listed address goes straight there, so the proxy never sees it.
- **Web ports on raw addresses bypass the proxy when both lists are `all`.** Tools can then reach any web server directly, with no domain check.
- **Raw traffic to a listed address bypasses the proxy on any port.** Web traffic sent through the proxy is checked by name on any port, including 8080. But a tool that connects directly to a listed IP on port 8080 or 8000 isn't checked by name.
- **Shared-hosting addresses serve other sites.** A listed address on a CDN can serve sites you never approved, and raw traffic reaches them too.
- **Pinned clients.** Pinned hosts pass through undecrypted, so their certificates work but their traffic isn't recorded. Other hosts are decrypted, so a client that pins a certificate and isn't on the pinned list will fail there.
- **Trust follows the Mac.** `setup` copies the trust roots from your Mac's keychains into the sandbox's image.
- **Credentials.** Logins and API keys live inside the sandbox. An agent that reads them can send them to any reachable host. Use test accounts, and keep the lists tight.
- **Port restrictions on the proxy.** The domain list matches hostnames, not ports.
- **The interface has no password.** While a sandbox runs with `--ui`, any process on your Mac can reach `127.0.0.1:6080`.
- **Anything inside an allowed host.** Allowed sites are reachable in full. Scope is still the operator's job.


## Troubleshooting

- **HTTPS fails inside the sandbox with a certificate error:** run `sudo ./install.sh` again. It re-exports the Mac's trust roots into the image.
- **`up` says the port is in use:** another process holds port 3128 on the gateway. `lsof -nP -iTCP:3128 -sTCP:LISTEN` shows which one.
- **`up` refuses a file:** the message names the file and the line, and the reason: malformed, an IP address in the domains file, too broad, overlapping the sandbox subnet, or belonging to this Mac.
- **`up` or `destroy` says a network won't delete:** the container service has a stale record for it, which `sandbox` can't remove. Reboot your Mac, then check with `container network list`.
- **A pf rule change didn't take effect:** the helper may be out of date. Run `sudo ./install.sh` again.
- **`sandbox` says this folder isn't a project:** run `sandbox init` in the project folder first, or `cd` into the project.
- **A sandbox is stopped:** run `sandbox start <name>` to bring it back with its saved settings.
- **Stray proxy processes:** `pgrep -fl mitmdump` lists them. `sandbox destroy` stops the proxy for its sandbox.

## For developers

The tool is one Bash script, `sandbox`, with a privileged helper, `privileged/sandbox-pf`. There's no daemon. Each command runs to completion. Per-machine state is in `~/.sandbox/`, and per-project state is in each project's `.sandbox/`.

- Check syntax after every change: `bash -n sandbox && bash -n privileged/sandbox-pf && bash -n install.sh && python3 -m py_compile addon/sbx_addon.py`
- Test each change end to end: `up`, `verify`, the traffic checks, then `destroy`. There are no automated tests yet.
- After changing the helper, reinstall it with `sudo ./install.sh`.
