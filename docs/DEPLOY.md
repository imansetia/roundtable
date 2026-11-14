# Deploying Roundtable to HermesVM (M3)

> **Read this first. Do not run any step until the Auditor has approved
> this guide.** Nothing in this guide is exposed to the internet until
> the steps in Part 7 run, and those steps only run after approval.
>
> **Who this is for:** the Owner, who is new to servers. Every step is
> one command, followed by what it does, what you should see, and how to
> undo it. If what you see does not match "Expected", **stop** — do not
> run the next step. Re-read the step, and ask the Auditor (or Muse) if
> it is still unclear.
>
> **What this guide never does:** it never stops, restarts, reconfigures
> or removes anything Roundtable did not create. In particular it never
> touches **Hermes** (the agent already running on this VM, listening on
> 127.0.0.1:51909). Several steps check that Hermes is still healthy
> before and after. It never touches the SSH-over-Tailscale rule.
>
> **How to run the commands:** connect over SSH via Tailscale as
> `azureuser`. Commands run as `azureuser` unless the step says `sudo`.
> Lines starting with `#` are comments, not commands. `<...>` is a
> placeholder you replace once, in Step 0.

## What you are building

```
  internet --(443/HTTPS)--> nginx --(127.0.0.1:8000)--> Roundtable (systemd)
                              |                            |
                              +-- 80: ACME challenge       +-- SQLite: /srv/roundtable/data
                                  + redirect to HTTPS      +-- tokens: /srv/roundtable/agents.json
```

- **Roundtable** keeps binding to `127.0.0.1:8000` only. Nothing else on
  the machine — and nobody on the internet — can reach it directly.
- **nginx** is the only thing listening publicly (80 and 443). It
  exposes exactly one path, `/mcp`, and returns 404 for everything else.
- **systemd** runs Roundtable as its own user (`roundtable`, no login,
  no sudo), restarts it if it crashes, and caps its memory.
- **Let's Encrypt (certbot)** provides the HTTPS certificate and renews
  it automatically.
- **ufw + the Azure NSG** allow only 80/tcp and 443/tcp from the
  internet. SSH stays Tailscale-only, and port 8000 stays closed.

Expected new memory use (measured on a machine like this one): the
Roundtable process uses about **70 MiB**; nginx uses about **10–20 MiB**
total; certbot uses nothing while idle (it only runs during renewal).
Step 23 measures the real numbers on your VM after install.

## Step 0 — Set the names you will reuse

**Command:**

```bash
PUBLIC_NAME=roundtable-k7q2.eastasia.cloudapp.azure.com
EMAIL=you@example.com
AUDITED_COMMIT=<paste the commit hash the Auditor gives you after approving the guide>
test -n "$PUBLIC_NAME" && test -n "$EMAIL" && test -n "$AUDITED_COMMIT" && echo ok
```

**What it does:** stores your public DNS name, your email address, and
the audited code version in shell variables, so later commands can use
`$PUBLIC_NAME`, `$EMAIL` and `$AUDITED_COMMIT` instead of you retyping
them. `PUBLIC_NAME` is the DNS name already set for this VM (the
Auditor confirmed it: `roundtable-k7q2.eastasia.cloudapp.azure.com`,
static IP 20.255.51.158). `EMAIL` is where Let's Encrypt sends expiry
warnings — replace `you@example.com` with your real address.
`AUDITED_COMMIT` is the exact commit the Auditor approved — a
40-character hash. The last line checks none of the three is empty: an
empty value would silently break later steps (an empty
`ROUNDTABLE_ALLOWED_HOSTS` makes the server refuse to start, and an
empty `server_name` makes nginx reject the config).

**Expected:** `ok`. If the last line prints nothing, one of the three
variables is empty — fix it before going on. (If you have not been
given the audited commit yet, the guide is not ready to run: ask the
Auditor first.)

**Undo:** close the SSH session (the variables are gone), or run
`unset PUBLIC_NAME EMAIL AUDITED_COMMIT`.

> **Note:** these variables live only in this SSH session. If you open a
> new terminal later, run Step 0 again first.

## Part 0 — Getting connected (Tailscale + SSH)

Before any step that touches the VM, get a working SSH session
over Tailscale. Every command in this guide runs in that session,
as `azureuser`, unless the step says `sudo`. If you already have
a session open from earlier, check it is still alive — and
remember Step 0's variables do not survive a new terminal, so run
Step 0 again first in a fresh one.

### Check Tailscale is up

**Command (on your own computer):**

```bash
tailscale status
```

**Expected:** a list of machines, including the VM. If instead
the command says you are not connected, there are two common
causes:

- **Logged out:** `tailscale status` prints `Logged out.`
  Sign in again — on Windows, open the Tailscale app (tray
  icon) and choose **Log in**; on the VM, run
  `sudo tailscale up` and follow the login link it prints.
  Then run `tailscale status` again.
- **Socket permission error:** the command prints
  `failed to connect to local tailscaled`. It cannot talk to
  the Tailscale daemon as your user. Run it with sudo —
  `sudo tailscale status` — and if the daemon itself is down,
  `sudo systemctl status tailscaled` tells you. Do not chmod
  the socket by hand.

### Find the VM host

The helper scripts default to the VM's Tailscale address,
`100.75.20.68` (the `VM_HOST` default in
`scripts/leader-token.sh` and `scripts/copy-worker-token.sh`).
If SSH fails with `Connection timed out` or `No route to host`,
the address may have changed: run `tailscale status` on your
computer, find the VM in the list (the machine you SSH into as
`azureuser`), and use its address — pass it as the first
argument (`bash scripts/copy-worker-token.sh <address>`) or set
`VM_HOST=<address>` for the session. Do not guess the address.

### SSH in

From Git Bash, use Windows OpenSSH (see "Which ssh" below):

```bash
/c/Windows/System32/OpenSSH/ssh.exe -i ~/.ssh/azure-hermes azureuser@100.75.20.68
```

From PowerShell, plain `ssh` is already Windows OpenSSH, so the
same login is shorter:

```powershell
ssh -i ~/.ssh/azure-hermes azureuser@100.75.20.68
```

**Expected:** a shell prompt on the VM. The key is
`~/.ssh/azure-hermes`. SSH works only over Tailscale — the
public port 22 is closed, so a connection without Tailscale
times out by design, not by mistake.

### Which ssh: Windows OpenSSH, not Git's

The helper scripts call Windows OpenSSH explicitly
(`/c/Windows/System32/OpenSSH/ssh.exe`) — Git's ssh is refused
by this VM, so a plain `ssh` typed in Git Bash is the wrong
program. Always run the scripts (they pick the right ssh), or
use the full Windows OpenSSH path above. The same applies to the
key: it lives at `~/.ssh/azure-hermes` on your computer.

### If a paste breaks: bracketed-paste markers

Long pasted commands fail in Git Bash: the terminal inserts
bracketed-paste markers (`[200~` … `~`) around a long paste and
the command breaks. So this guide's steps tell you the short
command to **type** (not paste). If a paste ever shows `[200~`
markers, press `Ctrl+C` and type the short command instead.
Never paste a token — use the no-display routes in CONNECT.md
Part 2.

## Part 1 — Check the VM first (check, don't assume)

### Step 1 — Confirm you are on the right machine

**Command:**

```bash
hostname && uptime && whoami
```

**What it does:** prints the machine name, how long it has been up, and
that you are logged in as `azureuser`.

**Expected:** something like `HermesVM`, an uptime, and `azureuser`. If
the hostname is not the VM you expect, stop.

**Undo:** nothing to undo (read-only check).

### Step 2 — Record that Hermes is healthy (before state)

**Command:**

```bash
ps -o pid,rss,etime,comm -C hermes; ss -tlnp | grep 51909
```

**What it does:** shows the Hermes process (its PID, memory, how long
it has run) and confirms something is listening on 127.0.0.1:51909.
Write the PID down — you will compare it at the end.

**Expected:** one `hermes` process line, and one line like
`LISTEN 0 4096 127.0.0.1:51909`. If either is missing, **stop and ask
the Auditor** — do not deploy on a machine whose existing agent is not
running.

**Undo:** nothing to undo (read-only check).

### Step 3 — Confirm ports 80, 443 and 8000 are free

**Command:**

```bash
ss -Htln 'sport = :80 or sport = :443 or sport = :8000'
```

**What it does:** lists anything already listening on those three ports.
The `-H` flag hides the header line, so "no output" below is literally
true. This check is only valid **before Step 11** (installing nginx):
apt starts nginx on port 80 at once, so after Step 11 port 80 is
supposed to be taken.

**Expected:** no output at all (all three are free). If something is
listening, stop and ask — do not kill it yourself.

**Undo:** nothing to undo (read-only check).

### Step 4 — Check the firewall rules you must not touch

**Command:**

```bash
sudo ufw status verbose
```

**What it does:** shows ufw's current rules.

**Expected:** `Status: active`, `Default: deny (incoming), allow
(outgoing)`, and a rule allowing `22/tcp` on interface `tailscale0`
(and nothing else allowing inbound traffic). Write this down: after the
deployment the only new rules must be `80/tcp` and `443/tcp`.

**Undo:** nothing to undo (read-only check).

### Step 5 — Check disk, memory and Python

**Command:**

```bash
df -h / && free -h && python3 --version
```

**What it does:** shows free disk, free memory, and the Python version.

**Expected:** at least a few GB free on `/` (the VM has about 53 GB
free), about 300 MiB or more available memory, and `Python 3.12.3`.
If memory available is under 200 MiB, stop and ask — the VM is too
full for another service.

**Undo:** nothing to undo (read-only check).

### Step 6 — Confirm nginx and certbot are not installed yet

**Command:**

```bash
which nginx certbot; dpkg -l | grep -E 'nginx|certbot' || true
for pkg in nginx certbot python3-venv sqlite3 git; do dpkg-query -W -f='${Package}: ${Status}\n' "$pkg" 2>/dev/null || echo "$pkg: not installed"; done | tee ~/roundtable-packages-before.txt
```

**What it does:** looks for existing nginx/certbot installs, then
records which of the five packages this guide is about to install are
already on the machine, in `~/roundtable-packages-before.txt`. The
record matters for the undo in Step 11: packages that were already
there must never be removed (Hermes or the system may use them).

**Expected:** no output from `which`, and no package lines from `dpkg`
(or only lines starting with `un`, meaning "not installed"). The second
command prints five lines, e.g. `nginx: not installed`,
`git: install ok installed`. If nginx or certbot is already installed,
stop and ask — this guide assumes a clean slate and will not
reconfigure an existing setup blindly.

**Undo:** nothing to undo (read-only check). The record file stays in
your home directory until you delete it.

### Step 7 — Confirm fail2ban and unattended-upgrades are running

**Command:**

```bash
systemctl is-active fail2ban unattended-upgrades
```

**What it does:** asks systemd whether those two services are active.

**Expected:** two lines, both `active`. These protect the VM (brute
force protection, automatic security updates). This guide never touches
them.

**Undo:** nothing to undo (read-only check).

## Part 2 — DNS name and Azure firewall (in the Portal)

Do this part in your browser, in the Azure Portal. Nothing is installed
on the VM here.

### Step 8 — Set the Public IP DNS label

**Command:** (in the Portal, no terminal command)

1. Open the VM in the Azure Portal → **Networking** → click the **Public
   IP address**.
2. Click **Configuration** → under **DNS name label**, type your `<label>`
   (lowercase letters and numbers, e.g. `mylabel`).
3. Click **Save**.

**What it does:** gives the VM's public IP a name:
`<label>.<region>.cloudapp.azure.com` — the `$PUBLIC_NAME` from Step 0.

**Expected (back in the terminal):**

```bash
getent hosts "$PUBLIC_NAME"
```

should print the VM's public IP address and the name. If it prints
nothing, wait a minute and try again; DNS can take a moment. If it
still prints nothing, the label in Step 0 does not match the one you
saved — fix Step 0 and try again.

**Undo:** in the Portal, delete the DNS name label and Save.

### Step 9 — Open ports 80 and 443 in the Azure NSG (and nothing else)

**Command:** (in the Portal, no terminal command)

1. In the VM's **Networking** blade, under **Network settings**, click
   **Create port rule** → **Inbound port rule**.
2. Fill in: Source **Any**; Source port ranges `*`; Destination **Any**;
   Destination port ranges `80`; Protocol **TCP**; Action **Allow**;
   Priority `100`; Name `AllowHTTP`. Click **Add**.
3. Repeat with Destination port ranges `443`, Priority `110`, Name
   `AllowHTTPS`.

**What it does:** tells Azure's network firewall to let internet traffic
reach the VM on ports 80 (HTTP, only for the certificate challenge and
the HTTPS redirect) and 443 (HTTPS).

**Expected:** the Networking blade lists the two new rules. Do **not**
add a rule for port 22 (SSH must stay closed publicly — it is reachable
only over Tailscale) and do **not** add a rule for port 8000 (the app
must never be reachable directly).

**Undo:** in the Portal, delete the `AllowHTTP` and `AllowHTTPS` rules.

## Part 3 — Install the packages

### Step 10 — Update the package list

**Command:**

```bash
sudo apt update
```

**What it does:** downloads the latest list of available packages. It
installs nothing.

**Expected:** ends with lines like `Reading package lists... Done` and
no errors. Warnings about a package list are fine; errors are not — if
you see `E:`, stop and ask.

**Undo:** nothing to undo.

### Step 11 — Install nginx, certbot, git, venv support and sqlite3

**Command:**

```bash
sudo apt install -y nginx certbot python3-venv sqlite3 git
```

**What it does:** installs the web server (nginx), the Let's Encrypt
client (certbot), Python's venv module, the SQLite command-line tool
(for backups), and git. **Note: installing nginx starts it at once**
(on port 80, with the default welcome site). That is harmless at this
point — port 80 is still blocked from the internet by ufw and the Azure
NSG (those rules come in Part 7) — but it means Step 30 must *reload*
nginx to load our site, not merely enable it. Nothing is configured
yet.

**Expected:** ends with `Processing triggers ...` and no errors.

**Undo:**

```bash
sudo apt remove -y <only the packages this guide installed>
sudo apt autoremove -y
```

(Only do this if you are abandoning the deployment entirely, and only
before any later step has used these packages.) Check
`~/roundtable-packages-before.txt` (Step 6) first and remove **only
the packages that were not installed before** — never `git` or
`python3-venv` if they were already installed, since Hermes or the system
may use them. For example, if the file says only nginx and certbot were
new, run `sudo apt remove -y nginx certbot`.

### Step 12 — Check the installed versions

**Command:**

```bash
nginx -v && certbot --version && python3 --version && git --version
```

**What it does:** prints each version.

**Expected:** four version lines, e.g. `nginx version: nginx/1.24.0`,
`certbot 2.x`, `Python 3.12.3`, `git version 2.x`. Exact numbers do not
matter.

**Undo:** nothing to undo (read-only check).


## Part 4 — Service user, directories, tokens and code

### Step 13 — Create the `roundtable` service user

**Command:**

```bash
sudo useradd --system --home-dir /srv/roundtable --shell /usr/sbin/nologin --comment "Roundtable service user" roundtable
```

**What it does:** creates a system user named `roundtable` that cannot
log in (no password, shell is `nologin`) and has no sudo. The app will
run as this user, so even if the app were compromised it could not act
as you or as root.

**Expected:** no output. Verify with:

```bash
id roundtable
```

which should print something like
`uid=999(roundtable) gid=999(roundtable) groups=999(roundtable)`.

**Undo:** (only after every file below is removed)

```bash
sudo userdel roundtable
```

### Step 14 — Create the directories and set their owners

**Command:**

```bash
sudo mkdir -p /srv/roundtable/app /srv/roundtable/data /srv/roundtable/backups /srv/roundtable/venv
sudo chown azureuser:azureuser /srv/roundtable/app /srv/roundtable/venv
sudo chown roundtable:roundtable /srv/roundtable/data /srv/roundtable/backups
sudo chmod 700 /srv/roundtable/data /srv/roundtable/backups
sudo chmod 755 /srv/roundtable /srv/roundtable/app /srv/roundtable/venv
sudo touch /srv/roundtable/agents.json /srv/roundtable/roundtable.env
sudo chown roundtable:roundtable /srv/roundtable/agents.json /srv/roundtable/roundtable.env
sudo chmod 600 /srv/roundtable/agents.json /srv/roundtable/roundtable.env
```

**What it does:** creates the four directories Roundtable uses and gives
each to its owner:
- `/srv/roundtable/app` — the git checkout (yours, so `git pull` works
  without sudo);
- `/srv/roundtable/venv` — the Python virtual environment (also yours:
  installing packages writes build files into the checkout, which the
  service user cannot do — and the service only needs to *read* the
  venv when it runs, which it can);
- `/srv/roundtable/data` — the database (the service user's, nobody
  else can even list it);
- `/srv/roundtable/backups` — database backups (same).
- The two secret files (`agents.json`, `roundtable.env`) are created
  empty here, owned by `roundtable`, mode 600 — because
  `/srv/roundtable` itself belongs to root, the service user could not
  create files in it, only write to files it already owns. Steps 15
  and 20 fill them in.

**Expected:** no output. Verify with:

```bash
ls -ld /srv/roundtable/app /srv/roundtable/data /srv/roundtable/backups /srv/roundtable/venv
```

`data` and `backups` must show `drwx------` (mode 700).

**Undo:**

```bash
sudo rm -rf /srv/roundtable/app /srv/roundtable/data /srv/roundtable/backups /srv/roundtable/venv
```

(Only as part of a full uninstall — see Part 10. This deletes the
database, so back it up first.)

### Step 15 — Generate the agent tokens (no shell history)

**Command:**

```bash
sudo -u roundtable python3 - <<'PYEOF'
import json, os, secrets
agents = {"agents": [
    {"name": "planner", "role": "leader", "skills": ["planning"],
     "token": secrets.token_urlsafe(48)},
    {"name": "coder", "role": "worker", "skills": ["python"],
     "token": secrets.token_urlsafe(48)},
]}
path = "/srv/roundtable/agents.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(agents, f, indent=2)
os.chmod(path, 0o600)
print("wrote", path)
PYEOF
```

**What it does:** creates `/srv/roundtable/agents.json` with two agents
(a leader `planner` and a worker `coder`) and a fresh random token for
each (64 characters, well over the 32-character minimum). The tokens are
generated inside Python and written straight to the file — they never
appear in your shell history and you never type them. The file is mode
600, owned by `roundtable`, and lives outside the git checkout, so it
can never be committed by accident.

**Expected:** `wrote /srv/roundtable/agents.json`. Verify with:

```bash
sudo ls -l /srv/roundtable/agents.json
```

which must show `-rw-------` (mode 600) and owner `roundtable`.

**Undo:**

```bash
sudo rm /srv/roundtable/agents.json
```

(This deletes the tokens. Every agent using them stops working until
you generate new ones.)

> **Later:** when you connect a real agent app (Claude, Muse, …), give
> it the token for its entry in this file. Read the file with
> `sudo cat /srv/roundtable/agents.json` and copy one token at a time.
> Never paste the whole file anywhere, and never put a token in a chat
> message, a screenshot, or a git commit. To add or rename agents, edit
> this file (exactly one leader, tokens at least 32 characters, no two
> agents sharing a token) and then restart the service with
> `sudo systemctl restart roundtable`.

### Step 16 — Clone the code

**Command:**

```bash
git clone https://github.com/imansetia/roundtable.git /srv/roundtable/app
git -C /srv/roundtable/app checkout "$AUDITED_COMMIT"
```

**What it does:** downloads the Roundtable code into
`/srv/roundtable/app`. The repository is public, so no login is needed.
Then it checks out exactly the commit the Auditor approved (Step 0) —
never just "whatever `main` happens to be today", so the deployed code
is the audited code. (The commit must already be pushed to GitHub; if
the checkout says the commit is not found, the bundle has not been
pushed yet — stop and ask.)

**Expected:** `Cloning into '/srv/roundtable/app'...` and a `done.`,
then no output from the checkout (it puts you on a detached HEAD at
that commit — that is intended). Verify with:

```bash
git -C /srv/roundtable/app rev-parse HEAD
```

which must print exactly `$AUDITED_COMMIT`, and `ls
/srv/roundtable/app` — you should see `README.md`, `SPEC.md`,
`roundtable/`, `worker.py`, `deploy/`, `docs/`, `tests/`.

**Undo:**

```bash
rm -rf /srv/roundtable/app
```

### Step 17 — Create the virtual environment (no system-wide packages)

**Command:**

```bash
python3 -m venv /srv/roundtable/venv
```

**What it does:** creates an isolated Python environment owned by you
(the operator). Python packages for Roundtable go here, never into the
system Python — Hermes and the OS keep their own packages untouched.
The service user runs the venv's Python but never writes to it.

**Expected:** no output. Verify with:

```bash
ls /srv/roundtable/venv/bin/python
```

**Undo:**

```bash
sudo rm -rf /srv/roundtable/venv
```

### Step 18 — Install Roundtable into the virtual environment

**Command:**

```bash
/srv/roundtable/venv/bin/pip install /srv/roundtable/app
```

**What it does:** installs Roundtable and its dependencies (the MCP SDK,
uvicorn, httpx) into the venv, from the checkout. This downloads
packages from PyPI — the only step that needs the internet for code.
(You may see a warning about the pip cache directory — it is harmless:
the cache is simply disabled for this install.)

**Expected:** ends with `Successfully installed roundtable ...` and no
errors.

**Undo:**

```bash
/srv/roundtable/venv/bin/pip uninstall -y roundtable
```

### Step 19 — Check the install works

**Command:**

```bash
/srv/roundtable/venv/bin/python -c "import roundtable, httpx; print('ok')"
```

**What it does:** imports the installed package (and httpx, which
`worker.py` needs) without starting anything.

**Expected:** `ok`. Anything else means the install failed — stop and
ask.

**Undo:** nothing to undo (read-only check).

### Step 20 — Install the EnvironmentFile and set your public name

**Command:**

```bash
sudo cp /srv/roundtable/app/deploy/roundtable.env.example /srv/roundtable/roundtable.env
sudo chown roundtable:roundtable /srv/roundtable/roundtable.env
sudo chmod 600 /srv/roundtable/roundtable.env
sudo sed -i "s/roundtable\.example\.com/$PUBLIC_NAME/" /srv/roundtable/roundtable.env
```

**What it does:** copies the template settings file into place, makes it
readable only by the service user, and replaces the placeholder with
your real public DNS name. The important line this sets is
`ROUNDTABLE_ALLOWED_HOSTS=<your name>`: without it, the app would
reject every request arriving through nginx with HTTP 421 (the MCP
SDK's DNS-rebinding protection only allows loopback hosts by default).
The other lines pin the app to `127.0.0.1:8000`, point it at the
database and the tokens file, and keep the 60/minute per-token rate
limit.

**Expected:** no output. Verify with:

```bash
sudo cat /srv/roundtable/roundtable.env
```

`ROUNDTABLE_ALLOWED_HOSTS` must show your real name, not the
placeholder. If it still says `roundtable.example.com`, the `sed`
found nothing to replace — either Step 0's `PUBLIC_NAME` was never set
(run Step 0 again, then repeat this step) or it is empty, in which
case the `sed` leaves the value empty (`ROUNDTABLE_ALLOWED_HOSTS=`
with nothing after it): the server will refuse to start with an empty
allowed-hosts list, which is the safe failure — fix Step 0 and repeat
this step.

**Undo:**

```bash
sudo rm /srv/roundtable/roundtable.env
```

## Part 5 — The systemd service

### Step 21 — Read the service file before you install it

**Command:**

```bash
cat /srv/roundtable/app/deploy/roundtable.service
```

**What it does:** shows you the unit file. Read it once, and check the
table below — every line is there for a reason. (The same explanations
are comments inside the file.)

| Line | What it does | Why it is there |
|---|---|---|
| `User=roundtable`, `Group=roundtable` | Runs as the service user, not root, not you | A compromised app cannot act as root or read your files |
| `WorkingDirectory=/srv/roundtable/app` | The process starts in the checkout | Predictable relative paths |
| `EnvironmentFile=/srv/roundtable/roundtable.env` | Loads the settings file | Secrets stay out of the unit file and out of git |
| `ExecStart=/srv/roundtable/venv/bin/python -m roundtable` | Starts the app from the venv | Uses only the isolated packages |
| `Restart=on-failure`, `RestartSec=5` | Restarts 5 s after a crash, forever | The board comes back by itself |
| `UMask=0077` | Files the service creates are owner-only | The database is created mode 600 |
| `MemoryAccounting=true` | systemd tracks the service's memory use | So `systemctl status` shows the live `Memory:` line used in Step 26 |
| `MemoryMax=200M` | Kills the service if it passes 200 MiB | Measured use is ~70 MiB; 200 MiB is ~3x headroom, and leaves the rest of the VM's ~320 MiB free for Hermes and the system |
| `MemorySwapMax=0` | The service gets no swap at all | `MemoryMax` does not cover swap, and this VM is already swapping — without this, Roundtable could push Hermes's pages out to swap |
| `OOMScoreAdjust=500` | If the kernel itself runs out of RAM, kill Roundtable first | Hermes keeps the default score 0; higher score = killed first |
| `NoNewPrivileges=true` | The process can never gain privileges | Even if it executed a setuid program by accident |
| `ProtectSystem=strict` | The whole filesystem is read-only for the app | It cannot modify code, config or system files |
| `ReadWritePaths=/srv/roundtable/data` | The one writable path: the data directory | The database (and its WAL files) must be writable |
| `ProtectHome=true` | No access to `/home` at all | Hermes and your files live there |
| `PrivateTmp=true` | A private `/tmp` | No sharing temp files with other processes |
| `PrivateDevices=true` | No hardware devices | The app needs none |
| `ProtectKernelTunables=true`, `ProtectKernelModules=true`, `ProtectControlGroups=true` | Cannot change kernel settings, load modules, or touch cgroups | The app needs none of these |
| `ProtectHostname=true`, `ProtectClock=true`, `ProtectKernelLogs=true` | Cannot change the hostname/clock or read kernel logs | The app needs none of these |
| `LockPersonality=true` | Cannot change kernel personality | The app needs none of this |
| `RemoveIPC=true` | No shared memory left behind after a crash | Clean restarts |
| `CapabilityBoundingSet=` (empty) | Drops every Linux capability | Binding to 127.0.0.1:8000 needs no privileges (only ports below 1024 do) |
| `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` | Only Unix/IPv4/IPv6 sockets | No raw or packet sockets |
| `SystemCallFilter=@system-service`, `SystemCallErrorNumber=EPERM` | Only the syscalls a normal network service needs; anything else is refused | Shrinks what a compromised process could do |

**Expected:** the file prints. No change is needed — do not edit it.

**Undo:** nothing installed yet.

### Step 22 — Install and start the service

**Command:**

```bash
sudo cp /srv/roundtable/app/deploy/roundtable.service /etc/systemd/system/roundtable.service
sudo systemctl daemon-reload
sudo systemctl enable --now roundtable
```

**What it does:** copies the unit file into systemd, reloads systemd's
configuration, then enables the service (start on boot) and starts it
now.

**Expected:** no output. Verify with:

```bash
systemctl status roundtable --no-pager
```

which should show `Active: active (running)` and a `Main PID`. If it
shows `failed`, do not continue — read the log (Step 23).

**Undo:**

```bash
sudo systemctl disable --now roundtable
sudo rm /etc/systemd/system/roundtable.service
sudo systemctl daemon-reload
```

### Step 23 — Read the log and confirm the app is listening

**Command:**

```bash
sudo journalctl -u roundtable -n 20 --no-pager
```

**What it does:** shows the service's last 20 log lines.

**Expected:** a line like
`Uvicorn running on http://127.0.0.1:8000` (and above it `Started
server process` / `Waiting for application startup`). The app itself
logs no "listening" line — Uvicorn's line is the confirmation.
If you see a Python traceback instead, stop and read it — the usual
causes are a missing `/srv/roundtable/roundtable.env` (Step 20) or a
bad `agents.json` (Step 15).

**Undo:** nothing to undo (read-only check).

### Step 24 — Test the app locally (no token → 401)

**Command:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/mcp -H "Content-Type: application/json" -d '{}'
```

**What it does:** sends a request to the app with no bearer <redacted>.

**Expected:** `401`. That is correct: the auth gate answers before the
app processes anything.

**Undo:** nothing to undo (read-only check).

### Step 25 — Test the app locally with a token (→ 400, not 421)

**Command:**

```bash
TOKEN=$(sudo python3 -c "import json; print(json.load(open('/srv/roundtable/agents.json'))['agents'][0]['token'])")
printf 'Authorization: Bearer %s\n' "$TOKEN" | curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/mcp -H @- -H "Content-Type: application/json" -d '{}'
unset TOKEN
```

**What it does:** reads the leader token from the file into a shell
variable — the token never enters your shell history — then sends an
authenticated request with the header passed on stdin (`curl -H @-`),
so the token never appears on curl's command line, where `ps` could
show it to other local users for a moment. Then it forgets the
variable. The body `{}` is not a valid MCP message, so `400` means
"you reached the app and it understood the request enough to reject
the body". `421` here would mean the Host check rejected you (wrong
allowed-hosts setting).

**Expected:** `400`.

**Undo:** the `unset TOKEN` at the end is the undo.

### Step 26 — Measure the memory (the number the 200M cap is based on)

**Command:**

```bash
ps -o pid,rss,comm -u roundtable; systemctl show roundtable -p MemoryMax; free -h
```

**What it does:** shows the service's process and its RSS (resident
memory) — the reliable measurement — confirms the 200 MiB cap is set
(`MemoryMax=209715200` bytes), and shows the VM's free memory. On a
normal VM, `systemctl status roundtable` also shows a `Memory:` line
(the unit turns memory accounting on); if that line is empty, the `ps`
line above is the measurement — the cap is still enforced.

**Expected:** the service uses about **70–120 MiB** (RSS around
70,000 KB). nginx has been running since Step 11 (using ~10–20 MiB),
so `free` should show only slightly less than Step 5. If the service
uses more than 150 MiB, stop and ask — something is wrong; do not raise
the cap yourself.

**Undo:** nothing to undo (read-only check).

## Part 6 — nginx, phase 1 (HTTP only, for the certificate)

### Step 27 — Install the HTTP-only site

**Command:**

```bash
sudo cp /srv/roundtable/app/deploy/nginx/roundtable-http-only.conf /etc/nginx/sites-available/roundtable
sudo sed -i "s/roundtable\.example\.com/$PUBLIC_NAME/" /etc/nginx/sites-available/roundtable
sudo ln -s /etc/nginx/sites-available/roundtable /etc/nginx/sites-enabled/roundtable
```

**What it does:** installs the phase-1 site file (port 80 only: it
serves the Let's Encrypt challenge and redirects everything else to
HTTPS), replaces the placeholder with your real DNS name, and enables
the site.

**Expected:** no output. Verify the name was replaced:

```bash
grep server_name /etc/nginx/sites-available/roundtable
```

It must show your real name, not `roundtable.example.com`.

**Undo:**

```bash
sudo rm /etc/nginx/sites-enabled/roundtable /etc/nginx/sites-available/roundtable
```

### Step 28 — Disable the default nginx site

**Command:**

```bash
sudo rm /etc/nginx/sites-enabled/default
```

**What it does:** removes the symlink that enables Ubuntu's default
nginx welcome page. Without this, a request to the VM with an unknown
Host would get the welcome page instead of your rules. This only
removes a symlink, not the file itself
(`/etc/nginx/sites-available/default` stays on disk).

**Expected:** no output. Verify with:

```bash
ls /etc/nginx/sites-enabled/
```

It should list only `roundtable`.

**Undo:**

```bash
sudo ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
```

### Step 29 — Test the nginx config (always, before every reload)

**Command:**

```bash
sudo nginx -t
```

**What it does:** checks the nginx configuration for errors without
changing anything.

**Expected:**

```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

If it reports an error, **do not reload** — read the error, fix the
file, and run `nginx -t` again. Never skip this step.

**Undo:** nothing to undo (read-only check).

### Step 30 — Load the new site (reload, not enable --now)

**Command:**

```bash
sudo nginx -t && sudo systemctl reload nginx
sleep 2
systemctl is-enabled nginx
```

**What it does:** tests the config again, then tells the running nginx
to re-read it. This step is a *reload*, not a start: installing nginx
in Step 11 already started it — with the default welcome site. `enable
--now` would do nothing to a running nginx, and the default site would
stay loaded (Step 31 would then get the welcome page instead of the
redirect). The reload swaps in the phase-1 site. The `sleep 2` matters:
`reload` returns before the new workers serve, so a check run
immediately afterwards can still hit the old configuration. The second
command checks nginx is enabled (start on boot), which the package
install already did.

**Expected:** the `nginx -t` output (`syntax is ok`, `test is
successful`), no output from the reload, and `enabled` from
`is-enabled`. Verify nginx is actually serving the new site with:

```bash
systemctl is-active nginx && sudo ss -tlnp | grep -E ':80 '
```

`is-active` must print `active`, and `ss` must show nginx listening on
port 80.

**Undo:**

```bash
sudo systemctl disable --now nginx
```

### Step 31 — Check the redirect works locally

**Command:**

```bash
curl --resolve "$PUBLIC_NAME:80:127.0.0.1" -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://"$PUBLIC_NAME"/
```

**What it does:** asks nginx for `/` over plain HTTP. `--resolve`
makes curl connect to 127.0.0.1 (this VM) while sending
`Host: $PUBLIC_NAME`, so the request hits the named server block.
Testing the bare IP directly no longer works: the catch-all server
added in the M3 audit fixes drops such connections silently (444),
so a plain `curl http://127.0.0.1/` returns nothing (000).

**Expected:** `301 https://<your name>/` — the redirect to HTTPS. And the
challenge path is served, not redirected:

```bash
curl --resolve "$PUBLIC_NAME:80:127.0.0.1" -s -o /dev/null -w "%{http_code}\n" http://"$PUBLIC_NAME"/.well-known/acme-challenge/test
```

must print `404` (no challenge file exists yet — the point is that it
is *not* a 301).

**Undo:** nothing to undo (read-only check).

## Part 7 — Firewall, then the certificate

### Step 32 — Open port 80 in ufw

**Command:**

```bash
sudo ufw allow 80/tcp
```

**What it does:** adds an inbound rule for TCP port 80. ufw adds the
rule for both IPv4 and IPv6 (check the output of Step 34 to confirm).

**Expected:** `Rule added` (and `Rule added (v6)`).

**Undo:**

```bash
sudo ufw delete allow 80/tcp
```

### Step 33 — Open port 443 in ufw

**Command:**

```bash
sudo ufw allow 443/tcp
```

**What it does:** adds an inbound rule for TCP port 443 (HTTPS).

**Expected:** `Rule added` (and `Rule added (v6)`).

**Undo:**

```bash
sudo ufw delete allow 443/tcp
```

### Step 34 — Verify the firewall (and that the SSH rule is untouched)

**Command:**

```bash
sudo ufw status verbose
```

**What it does:** shows all ufw rules.

**Expected:** the original `22/tcp` rule on `tailscale0` is still there
and unchanged, and you now also see `80/tcp` and `443/tcp` ALLOW IN
(each once for v4 and once for v6). If the 22/tcp rule is missing or
changed, **stop immediately** and ask the Auditor — do not continue,
or you may lock yourself out of SSH.

**Undo:** nothing to undo (read-only check).

### Step 35 — Issue the certificate with certbot (webroot)

**Command:**

```bash
sudo certbot certonly --webroot -w /var/www/html -d "$PUBLIC_NAME" --agree-tos --no-eff-email -m "$EMAIL" --deploy-hook "systemctl reload nginx"
```

**What it does:** asks Let's Encrypt for a certificate for your public
name. The `--webroot` method writes a small answer file into
`/var/www/html/.well-known/acme-challenge/`; Let's Encrypt fetches it
over port 80 through nginx (phase 1 serves that path) to prove you own
the name. `--agree-tos` accepts the terms, `-m` gives your email for
expiry warnings, `--no-eff-email` declines sharing it with the EFF.
`--deploy-hook "systemctl reload nginx"` is the important part most
guides forget: `certonly` has no installer, so without it a later
`certbot renew` would write the new certificate files but nginx would
keep serving the old one from memory — and about 90 days after install
the site would start failing with an expired certificate. The hook is
stored in the renewal config, so every future renewal reloads nginx
automatically. certbot runs once and exits — it does not stay running.

**Expected:** ends with

```
Congratulations! Your certificate and chain have been saved at:
  /etc/letsencrypt/live/<your name>/fullchain.pem
```

If it fails, the usual causes are: the DNS name does not resolve to
this VM yet (Step 8), the NSG rules are missing (Step 9), or ufw is
blocking port 80 (Steps 32–34). Fix the cause and run the command
again — do not continue without a certificate.

**Undo:**

```bash
sudo certbot delete --cert-name "$PUBLIC_NAME"
```

### Step 36 — Check the certificate files exist

**Command:**

```bash
sudo ls -l /etc/letsencrypt/live/"$PUBLIC_NAME"/
```

**What it does:** lists the certificate directory.

**Expected:** `fullchain.pem` and `privkey.pem` (among others), owned
by root. `privkey.pem` must never be copied anywhere or pasted
anywhere.

**Undo:** nothing to undo (read-only check).

## Part 8 — nginx, phase 2 (the full HTTPS site)

### Step 37 — Install the full site

**Command:**

```bash
sudo cp /srv/roundtable/app/deploy/nginx/roundtable.conf /etc/nginx/sites-available/roundtable
sudo sed -i "s/roundtable\.example\.com/$PUBLIC_NAME/g" /etc/nginx/sites-available/roundtable
```

**What it does:** replaces the phase-1 site file with the full HTTPS
site (the phase-1 file is Roundtable's own file, so replacing it is
fine) and puts your real name in every place, including the two
certificate paths.

**Expected:** no output. Verify with:

```bash
grep -E 'server_name|ssl_certificate' /etc/nginx/sites-available/roundtable
```

Every line must show your real name — no `roundtable.example.com`
anywhere.

**Undo:** reinstall the phase-1 file (Step 27) and reload (Step 39).

### Step 38 — Test the config again (always, before every reload)

**Command:**

```bash
sudo nginx -t
```

**What it does:** same as Step 29, for the new file.

**Expected:** `syntax is ok` and `test is successful`. If it fails,
the usual cause is a certificate path that does not match Step 36 —
fix it and test again. Do not reload a failing config.

**Undo:** nothing to undo (read-only check).

### Step 39 — Reload nginx (reload, never restart)

**Command:**

```bash
sudo systemctl reload nginx
sleep 2
```

**What it does:** tells the running nginx to re-read its configuration
without dropping connections. **Never use `restart` for nginx here:**
a restart briefly stops listening, and a restart after a bad config
can leave nginx down entirely — `reload` after a passing `nginx -t`
cannot do that. The `sleep 2` matters: `reload` returns before the
new workers serve, so a check run immediately afterwards can still hit
the old configuration.

**Expected:** no output. Verify with:

```bash
systemctl is-active nginx && sudo ss -tlnp | grep -E ':80 |:443 '
```

`is-active` must print `active`, and `ss` must show nginx on both
ports.

**Undo:** reinstall the phase-1 file (Step 27), `nginx -t`, reload.

### Step 40 — Check HTTPS from the VM

**Command:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://"$PUBLIC_NAME"/mcp -H "Content-Type: application/json" -d '{}'
```

**What it does:** asks for the MCP path over HTTPS, with no token.

**Expected:** `401`. And the other checks:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://"$PUBLIC_NAME"/anything-else
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://"$PUBLIC_NAME"/
```

The first must print `404` (only `/mcp` is exposed) and the second
`301 https://<your name>/` (plain HTTP redirects to HTTPS).

**Undo:** nothing to undo (read-only check).

### Step 41 — Check an authenticated request through nginx

**Command:**

```bash
TOKEN=$(sudo python3 -c "import json; print(json.load(open('/srv/roundtable/agents.json'))['agents'][0]['token'])")
printf 'Authorization: Bearer %s\n' "$TOKEN" | curl -s -o /dev/null -w "%{http_code}\n" -X POST https://"$PUBLIC_NAME"/mcp -H @- -H "Content-Type: application/json" -d '{}'
unset TOKEN
```

**What it does:** the same check as Step 25, but through nginx over
HTTPS. This is the check that actually proves the deployment works:
Step 40's requests have no token, so they get 401 from the auth gate
*before* the Host check runs — a wrong `ROUNDTABLE_ALLOWED_HOSTS`
would pass every check so far and only show up later as 421. Here the
token is sent the same way as in Step 25 (header on stdin, never on
the command line), and the body `{}` is not a valid MCP message.

**Expected:** `400, not 421`. `400` means the request reached the app
through the proxy and the app rejected only the body. `421` means the
Host check rejected you: the name in `ROUNDTABLE_ALLOWED_HOSTS`
(Step 20) does not match the name nginx sends — fix the
EnvironmentFile, then `sudo systemctl restart roundtable`, and repeat
this step.

**Undo:** the `unset TOKEN` at the end is the undo.

### Step 42 — Check automatic renewal works

**Command:**

```bash
systemctl list-timers --all | grep certbot
sudo grep -r renew_hook /etc/letsencrypt/renewal/
sudo certbot renew --dry-run
```

**What it does:** first shows certbot's renewal timer (installed with
the certbot package; it runs `certbot renew` twice a day and renews
any certificate due to expire within 30 days, using the webroot method
saved in step 35 — nginx keeps serving the challenge path, so renewal
needs no downtime). Then it checks the renewal config actually
contains the reload hook from Step 35. Note the key is `renew_hook`,
not `deploy_hook`: certbot stores the `--deploy-hook` flag under the
name `renew_hook` in the renewal config, so `grep deploy_hook` finds
nothing even though the hook is present. Finally it simulates a
renewal against Let's Encrypt's staging server.

**Expected:** the timer line shows `certbot.timer` with a next-run
time, the `grep` prints a line with `renew_hook`, and the dry run ends
with `Congratulations, all simulated renewals succeeded`. The dry run
takes a minute or so — be patient. If the `grep` prints nothing, the
hook was not stored: re-run Step 35 with the `--deploy-hook` flag and
come back here.

**Undo:** nothing to undo (read-only check; the dry run issues no real
certificate).

## Part 9 — Backup and restore

### Step 43 — Back up the database (safe while the server runs)

**Command:**

```bash
sudo -u roundtable sqlite3 /srv/roundtable/data/roundtable.db ".backup /srv/roundtable/backups/roundtable-$(date +%F).db"
sudo chmod 600 /srv/roundtable/backups/roundtable-"$(date +%F)".db
sudo -u roundtable ls -l /srv/roundtable/backups/
```

**What it does:** copies the live database with SQLite's online backup
(`.backup` reads a consistent snapshot — it is safe while the server
is running; never copy the `.db` file with plain `cp` while the server
writes to it). The backup is mode 600, in the backups directory. Run
this whenever the board has work you would not want to lose; there is
no automatic schedule yet (see Open questions in CHANGELOG.md).

**Expected:** a line like
`-rw------- 1 roundtable roundtable ... roundtable-2026-11-10.db`.

**Undo:** delete the backup file:

```bash
sudo rm /srv/roundtable/backups/roundtable-YYYY-MM-DD.db
```

> **Also back up the secrets, separately:** `agents.json` and
> `roundtable.env` are not in the database. Copy them into your
> password manager (not onto the VM in plaintext, not into git). If
> you lose `agents.json`, every agent token is lost with it.

### Step 44 — Restore a backup (only if the database is damaged)

**Command:**

```bash
sudo systemctl stop roundtable
sudo -u roundtable bash -c 'for suffix in "" "-wal" "-shm"; do f="/srv/roundtable/data/roundtable.db$suffix"; if [ -e "$f" ]; then mv "$f" "$f.before-restore"; fi; done'
sudo -u roundtable cp /srv/roundtable/backups/roundtable-YYYY-MM-DD.db /srv/roundtable/data/roundtable.db
sudo systemctl start roundtable
systemctl status roundtable --no-pager | head -n 5
```

**What it does:** stops the service (so nothing writes during the
restore), then moves the live database **and** its write-ahead log
(`roundtable.db-wal`) and shared-memory file (`roundtable.db-shm`)
aside — do not delete them — and only then copies the backup in as the
service user (so the restored file is owned by `roundtable`, mode 600).
The move-aside matters: after a crash (the case this step is for) a
stale WAL is usually still on disk, and SQLite would replay it onto
the restored file, corrupting the restore with the old, broken data.
Then it starts the service again. (Replace `YYYY-MM-DD` with the
backup's real date.)

**Expected:** `Active: active (running)` in the status output, and
Step 24's curl still returns `401`. Once you have confirmed the board
looks right, you may delete the `.before-restore` files:

```bash
sudo rm /srv/roundtable/data/roundtable.db.before-restore /srv/roundtable/data/roundtable.db-wal.before-restore /srv/roundtable/data/roundtable.db-shm.before-restore
```

(If a file does not exist, `rm` will complain — that is fine.)

**Undo:** restore the backup you made before this one (Step 43), or
re-run Step 44 with a different backup file. If the restore made
things worse and you have not deleted them yet, the moved-aside files
are your way back: stop the service, move the `.before-restore` files
back (dropping the suffix), and start it again.

## Part 10 — Updating, rollback, uninstall

### Step 45 — Record the current version before updating

**Command:**

```bash
git -C /srv/roundtable/app rev-parse HEAD | tee ~/roundtable-commit-before-update.txt
```

**What it does:** prints the commit the server currently runs and saves
it to a file in your home directory, so a rollback (Step 47) knows
exactly where to go back to.

**Expected:** a 40-character commit hash, e.g. `0399ab6...`.

**Undo:** nothing to undo (read-only check).

### Step 46 — Update to the audited version

**Command:**

```bash
git -C /srv/roundtable/app fetch origin
git -C /srv/roundtable/app checkout "$AUDITED_COMMIT"
git -C /srv/roundtable/app rev-parse HEAD
/srv/roundtable/venv/bin/pip install /srv/roundtable/app
sudo systemctl restart roundtable
systemctl status roundtable --no-pager | head -n 5
```

**What it does:** fetches the newest code from GitHub, then checks out
exactly the commit the Auditor approved for this update
(`$AUDITED_COMMIT` — set it in Step 0 to the hash the Auditor gives
you for the update; never `git pull` on `main` and go live with
whatever happens to be there). The `rev-parse` must print exactly
`$AUDITED_COMMIT`. Then it reinstalls the venv and restarts the
service (restart is correct here — it is Roundtable's own service, and
the new code only loads on a restart; the "reload, never restart" rule
is for nginx). Then checks it came back up.

**Expected:** `Active: active (running)`. Then re-run Step 24 (local
401 check) and Step 41 (authenticated check through nginx). If the
service fails to start, read the log (Step 23) — and roll back
(Step 47) rather than guessing.

**Undo:** Step 47.

### Updating to M5

M5 adds permanent review history: every successful `review_task`
stores a review message, and the `messages` table is migrated on
first start (see CHANGELOG.md "M5"). The update itself is the
normal one — back up first, then Step 46 with the new audited
commit:

1. **Back up the database first (Step 43).** Do not skip this:
   the migration rewrites the `messages` table, and the backup is
   the way back.
2. **Record the current version (Step 45)**, then set
   `$AUDITED_COMMIT` to the hash the Auditor gives you for M5 and
   run **Step 46** (fetch, check out, reinstall, restart
   Roundtable only). Nothing else is restarted — Hermes, nginx
   and certbot are untouched.
3. **Verify:** Step 24 (local 401 check) and Step 41
   (authenticated check through nginx) must pass, and the service
   must show `Active: active (running)`. The migration runs on
   first start; existing tasks keep their messages and get no
   back-filled review history.
4. **Reconnect both apps.** The restart drops the leader's MCP
   connection: in the leader session, open `/mcp` and choose
   **Reconnect** for `roundtable`. Muse's stored credential remains valid — reconnect it
   normally, and re-paste a token only after an actual rotation
   (CONNECT.md Step 16). If the service fails to start,
   roll back (Step 47) rather than guessing. If the migration
   itself failed (the log shows a database error), restore the
   backup (Step 44) as well as rolling back (Step 47) — the
   rollback alone only restores the code; the database stays
   migrated. (M4 code runs fine on a migrated table, so a rollback
   without a restore is only for code problems.)

**Undo:** Step 47 (and Step 44 if the migration failed).

### Step 47 — Roll back to the previous version

**Command:**

```bash
OLD=$(cat ~/roundtable-commit-before-update.txt)
git -C /srv/roundtable/app checkout "$OLD"
/srv/roundtable/venv/bin/pip install /srv/roundtable/app
sudo systemctl restart roundtable
systemctl status roundtable --no-pager | head -n 5
```

**What it does:** checks out the exact commit recorded in Step 45,
reinstalls it, and restarts. (To go to a newer audited version
afterwards, get its hash from the Auditor, set it as
`AUDITED_COMMIT`, and repeat Step 46 — never `git checkout main`.)

**Expected:** `Active: active (running)`, and Step 24 returns `401`.

**Undo:** Step 46.

### Updating to M6

M6 adds complete activity history: every submission is kept in a
new `submissions` table, and every tool call leaves an audit row
with its arguments (at most 64 KB) and, on error, the error
message (see CHANGELOG.md "M6"). The update itself is the normal
one — back up first, then Step 46 with the new audited commit:

1. **Back up the database first (Step 43).** Do not skip this:
   the migration adds columns to `audit_log` and a new table, and
   the backup is the way back.
2. **Record the current version (Step 45)**, then set
   `$AUDITED_COMMIT` to the hash the Auditor gives you for M6 and
   run **Step 46** (fetch, check out, reinstall, restart
   Roundtable only). Nothing else is restarted — Hermes, nginx
   and certbot are untouched.
3. **Verify:** Step 24 (local 401 check) and Step 41
   (authenticated check through nginx) must pass, and the service
   must show `Active: active (running)`. The migration runs on
   first start; existing rows are kept and get no back-filled
   history.
4. **Reconnect both apps.** The restart drops the leader's MCP
   connection: in the leader session, open `/mcp` and choose
   **Reconnect** for `roundtable`. Muse's stored credential remains valid — reconnect it
   normally, and re-paste a token only after an actual rotation
   (CONNECT.md Step 16). If the service fails to start,
   roll back (Step 47) rather than guessing. If the migration
   itself failed (the log shows a database error), restore the
   backup (Step 44) as well as rolling back (Step 47) — the
   rollback alone only restores the code; the database stays
   migrated.

**Database growth.** History is kept forever, so the database
grows with use: every submission stores the full result (up to
20 KB), and every audit row stores up to 64 KB of arguments. A
busy board can grow by megabytes per month. Check its size with:

```bash
sudo -u roundtable sh -c 'du -ch /srv/roundtable/data/roundtable.db*'
```

The glob matters: while the server runs, most of the data sits
in `roundtable.db-wal`, so plain `du -h roundtable.db` showed
4.0K on the VM. Watch the `total` line — the `.db` file plus
`-wal` and `-shm` together. The glob is expanded by a shell
running as `roundtable` (`sh -c` under `sudo`): `azureuser`'s
own shell cannot read the data directory (mode 700), so
expanding the glob before `sudo` matches nothing and `du` fails
with "No such file or directory".

and see what is in it with (read-only, as `roundtable`):

```bash
sudo -u roundtable sqlite3 -readonly /srv/roundtable/data/roundtable.db "SELECT COUNT(*) FROM submissions; SELECT COUNT(*) FROM audit_log;"
```

There is no pruning yet — that is a decision for the Owner, not
something this update does on its own.

**History cap (M6 audit fix F2).** The audit log's stored
arguments are capped by `ROUNDTABLE_HISTORY_MAX_MB` (default
1024 MB), set in `/srv/roundtable/roundtable.env` (Step 20) and
read at startup. Above the cap, audit rows keep agent, tool,
task, outcome and error but store a marker instead of arguments,
and the server logs a warning once — tools never fail because of
the cap. F1 closes the flood vector the audit found: a
rate-limited call stores no arguments at all (only a marker),
and a size-rejected call keeps a 2 KB prefix, not 64 KB. Check
the disk the database lives on next to the database itself:

```bash
df -h /
sudo -u roundtable sh -c 'du -ch /srv/roundtable/data/roundtable.db*'
```

**What to do when the cap is reached.** The board keeps working
and every call is still logged — only the arguments are omitted.
To restore full argument logging, raise
`ROUNDTABLE_HISTORY_MAX_MB` in `/srv/roundtable/roundtable.env`
and restart Roundtable (`sudo systemctl restart roundtable`),
or free disk first if the disk itself is full. Do not delete
history rows by hand: the log is append-only by design, and
pruning old history is a decision for the Owner.

**Disk check (weekly).** The cap bounds only the audit log's
stored arguments — the history rows themselves are uncapped:
every submission keeps its full result (up to 20 KB), and every
audit row keeps its agent, tool, task, outcome and error. So
check the disk once a week, even with the cap on. Hermes shares
this disk, so a board that grows unchecked can take Hermes down
with it. The VM had about 53 GB free when Roundtable was
deployed; act while there is still room:

```bash
df -h /
sudo -u roundtable sh -c 'du -ch /srv/roundtable/data/roundtable.db*'
```

Watch the `total` line of the `du` output against the free space
in `df -h /`. Below 10 GB free, investigate what is growing;
below 5 GB, act now — free disk space, and ask the Owner about
pruning history. Never delete history rows by hand: that is a
decision for the Owner, not a disk-cleanup step.

**Undo:** Step 47 (and Step 44 if the migration failed).

### Updating to M7

M7 enforces skills on the server: `claim_task` is rejected unless
the worker holds every required skill, and a task may require
several skills (`required_skills`, a list). The `tasks` table is
migrated on first start: `required_skill` (a single string)
becomes `required_skills` (a JSON list) — a set skill becomes a
one-item list, an unset one becomes [] (see CHANGELOG.md "M7").
The update itself is the normal one — back up first, then Step 46
with the new audited commit:

1. **Back up the database first (Step 43).** Do not skip this:
   the migration rebuilds the `tasks` table, and the backup is
   the way back.
2. **Record the current version (Step 45)**, then set
   `$AUDITED_COMMIT` to the hash the Auditor gives you for M7 and
   run **Step 46** (fetch, check out, reinstall, restart
   Roundtable only). Nothing else is restarted — Hermes, nginx
   and certbot are untouched.
3. **Verify:** Step 24 (local 401 check) and Step 41
   (authenticated check through nginx) must pass, and the service
   must show `Active: active (running)`. The migration runs on
   first start; existing tasks keep their skills, status,
   assignee, result and review note.
4. **Reconnect both apps.** The restart drops the leader's MCP
   connection: in the leader session, open `/mcp` and choose
   **Reconnect** for `roundtable`. Muse's stored credential remains valid — reconnect it
   normally, and re-paste a token only after an actual rotation
   (CONNECT.md Step 16). If the service fails to start,
   roll back (Step 47) rather than guessing.

**Rollback needs the backup too.** This migration is different
from M5 and M6: the rebuilt `tasks` table has no `required_skill`
column, so M6 code cannot run on a migrated database at all —
`create_task` and `list_tasks` both fail (the Auditor's probe:
M6 code started on a migrated database errors on every call).
So if the service fails after the update, or you roll back
(Step 47) for any reason, you must also restore the backup
(Step 44): the rollback alone only restores the code; the
database stays migrated, and the old code cannot read it. (M5
said M4 code runs fine on a migrated table — the opposite is
true here.)

**Undo:** Step 47 and Step 44 together.

### Step 48 — Full uninstall (removes only Roundtable's things)

Run these in order. **Back up the database first (Step 43)** — after
this, the board data is gone.

**Command:**

```bash
sudo systemctl disable --now roundtable
sudo rm /etc/systemd/system/roundtable.service
sudo systemctl daemon-reload
sudo rm /etc/nginx/sites-enabled/roundtable /etc/nginx/sites-available/roundtable
sudo nginx -t && sudo systemctl reload nginx
sleep 2
sudo certbot delete --cert-name "$PUBLIC_NAME"
sudo ufw delete allow 80/tcp
sudo ufw delete allow 443/tcp
sudo rm -rf /srv/roundtable
sudo userdel roundtable
```

**What it does:** stops and removes the service and its unit file,
removes the nginx site and reloads nginx, deletes the certificate,
removes the two ufw rules this guide added, deletes Roundtable's
directory tree (code, venv, database, backups, tokens, settings), and
deletes the service user. Then, in the Azure Portal, delete the
`AllowHTTP` and `AllowHTTPS` NSG rules from Step 9.

**Optional, if you want the VM back exactly as it was:**
- Restore the default nginx site (removed in Step 28):

```bash
sudo ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

- Remove the packages this guide installed — but only the ones that
  were not already there (check `~/roundtable-packages-before.txt`
  from Step 6 first; never remove `git` or `python3-venv` if they were
  already installed):

```bash
sudo apt remove -y nginx certbot
sudo apt autoremove -y
```

(Adjust the package list to what the record file says was new.)

**What it deliberately does not touch:** Hermes, fail2ban,
unattended-upgrades, Tailscale, the SSH rule, and the rest of the VM.

**Expected:** each command succeeds; `systemctl status roundtable`
afterwards prints `Unit roundtable.service could not be found`, and
`sudo ufw status verbose` shows only the original `22/tcp` rule.

**Undo:** there is none — this deletes data. That is why the backup
step comes first.

## Part 11 — Verification (for the Auditor)

Run these after the deployment, in order. Everything must pass before
the deployment is considered done.

### On the VM

```bash
# 1. Service is running, as the service user, memory within the cap.
systemctl status roundtable --no-pager | grep -E 'Active:'
ps -o pid,user,rss,comm -u roundtable
systemctl show roundtable -p MemoryMax
#    Expected: Active: active (running); one python process owned by
#    roundtable, RSS ~70-120M; MemoryMax=209715200. (On a normal VM,
#    `systemctl status` also shows a Memory: line.)

# 2. The app listens on loopback only; nginx on 80/443; nothing on 8000
#    from outside the VM.
sudo ss -tlnp | grep -E ':8000 |:80 |:443 |:51909 '
#    Expected: 127.0.0.1:8000 (python), 0.0.0.0:80 and 0.0.0.0:443
#    (nginx), 127.0.0.1:51909 (hermes). No 0.0.0.0:8000 line.
#    (sudo is needed: without it ss hides other users' process names.)

# 3. Hermes is still the same process as in Step 2, still listening.
ps -o pid,rss,etime,comm -C hermes; sudo ss -tlnp | grep 51909
#    Expected: the same PID as recorded in Step 2, still listening on
#    127.0.0.1:51909.

# 4. Firewall: only the SSH rule plus 80/443.
sudo ufw status verbose
#    Expected: 22/tcp on tailscale0 unchanged; 80/tcp and 443/tcp ALLOW
#    IN (v4 and v6); nothing else inbound.

# 5. Memory after install (compare with Step 26).
free -h
#    Expected: still a comfortable amount available (the new components
#    use ~80-140 MiB total).

# 6. Certificate renews automatically, and the renewal reloads nginx.
systemctl list-timers --all | grep certbot
sudo grep -r renew_hook /etc/letsencrypt/renewal/
sudo certbot renew --dry-run
#    Expected: certbot.timer listed; the grep prints a renew_hook line;
#    dry run succeeds.
```

### From outside the VM (your own computer, not the VM)

Run these in a POSIX shell — on Windows, enter Git Bash with
`& "C:\Program Files\Git\bin\bash.exe" --login` from PowerShell
(never bare `bash`: that starts WSL) — or with `curl.exe`
on Windows — PowerShell 5.1 treats `curl` as an alias for
`Invoke-WebRequest`, so the `-s`, `-o` and `-w` flags below fail
there.

```bash
# 7. No token -> 401 (auth still works through the proxy).
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://$PUBLIC_NAME/mcp -H "Content-Type: application/json" -d '{}'
#    Expected: 401

# 8. With a token -> 400, not 421: run Step 41 instead. That check
#    needs the leader token from /srv/roundtable/agents.json, a file
#    that exists only on the VM, so it cannot run from outside.
#    Expected: 400, not 421 (see Step 41).

# 9. Any other path -> 404 (only /mcp is exposed).
curl -s -o /dev/null -w "%{http_code}\n" https://$PUBLIC_NAME/
curl -s -o /dev/null -w "%{http_code}\n" https://$PUBLIC_NAME/anything
#    Expected: 404, 404

# 10. Plain HTTP redirects to HTTPS.
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://$PUBLIC_NAME/
#    Expected: 301 https://<your name>/

# 11. The certificate is valid and belongs to your name.
echo | openssl s_client -connect $PUBLIC_NAME:443 -servername $PUBLIC_NAME 2>/dev/null | openssl x509 -noout -subject -issuer -dates
#    Expected: subject CN = <your name>, issuer = Let's Encrypt, dates
#    valid (not expired, about 90 days).

# 12. Port 8000 is not reachable from outside.
curl --connect-timeout 5 -s -o /dev/null -w "%{http_code}\n" http://$PUBLIC_NAME:8000/mcp || echo "connection refused/timed out (expected)"
#    Expected: connection refused or timeout — never a 401 or 200.
```

### Rate limiting is unchanged

nginx only forwards the request, including the `Authorization` header,
unchanged. The server still limits **per token** (60 tool calls/minute
per token, counted on tool calls only — see SPEC.md section 7.3 and
`roundtable/server.py`). nginx's per-IP limit (`limit_req`, 10 req/s
per IP, burst 20) is a second, coarser layer: it can only trigger on
floods, far above the per-token limit. To verify the per-token limit
still works, run the repository's own test suite (`pytest tests/`) —
it contains a rate-limit test — rather than hammering the live server.

## If something goes wrong

- **`nginx -t` fails:** do not reload. Read the error line — it names
  the file and line number. The usual cause is a certificate path that
  does not exist yet (Step 36) or the placeholder not replaced
  (Steps 27/37).
- **Service fails to start:** `sudo journalctl -u roundtable -n 50
  --no-pager` and read the traceback. Common causes: a missing
  EnvironmentFile (Step 20), a bad `agents.json` (Step 15), or
  `ROUNDTABLE_ALLOWED_HOSTS` not matching the public name.
- **`certbot` fails:** check DNS (Step 8), the NSG rules (Step 9), and
  ufw (Step 34). Let's Encrypt must be able to reach port 80 from the
  internet.
- **401 where you expected 400:** the token is wrong or the request
  went to the wrong host. Re-check Step 25.
- **421:** the Host check rejected the request. The name in
  `ROUNDTABLE_ALLOWED_HOSTS` (Step 20) must match the name nginx sends
  (`proxy_set_header Host $host`), i.e. your public DNS name. Fix the
  EnvironmentFile, then `sudo systemctl restart roundtable`.
- **Hermes looks unhappy:** stop immediately and tell the Auditor what
  you ran. Do not try to fix Hermes from this guide — it is out of
  scope.
