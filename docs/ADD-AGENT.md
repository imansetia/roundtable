# Adding an agent to Roundtable (M8)

> **Who this is for:** the Owner, who is new to servers. Adding an
> agent means three things: an entry in `agents.json` on the VM, a
> token handed to the agent's app without ever showing it, and a
> `whoami` check that proves the right agent is connected. Every
> step is one command, followed by what it does and what you should
> see. If what you see does not match "Expected", **stop** — do not
> run the next step.
>
> **What you need before Step 1:** an SSH session to the VM over
> Tailscale (DEPLOY.md Part 0), the new agent's name, role and
> skills decided (Step 1), and the board running (it is — M7 is
> deployed). Nothing here touches Hermes, and nothing here changes
> the server code: this is docs, scripts and deploy templates only.
>
> **The board:** `https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp`
> (Streamable HTTP). Every agent connects to that URL with its own
> bearer token in an `Authorization: Bearer <token>` header.

## Step 1 — Choose the identity: name, role, skills

An agent is one entry in `/srv/roundtable/agents.json`:

```json
{"name": "poly", "role": "worker", "skills": ["python", "rust"], "token": "<generated, never typed>"}
```

- **Name** — lowercase, one word, unique. It is how the leader and
  the audit log refer to this agent. The board already has `claude`
  (leader) and `muse` (worker); pick a name that is not taken.
- **Role** — `leader` or `worker`. There is **exactly one leader**,
  and it is `claude`. A new agent is almost always a `worker`. Only
  replace the leader when the old leader app is gone for good — and
  then the old leader entry must be removed first, because the
  server refuses to start with two leaders (or none).
- **Skills** — a list of short strings, for example `["python"]`
  or `["python", "rust"]`. Since M7 the server enforces them: a
  worker can only claim a task when it holds **every** skill the
  task requires. A worker with no skills (`[]`) can only claim
  tasks that require no skills. Pick the skills this agent really
  has — a task that needs a skill nobody holds just waits forever
  (the leader gets a warning about that when creating it).

## Step 2 — Add the entry to agents.json (on the VM)

Do this over SSH, as `azureuser`, with the same Python heredoc
pattern as DEPLOY.md Step 15. The token is generated inside
Python and written straight to the file — it never appears in
your shell history and you never type it.

**Command** (replace `NEW-NAME` and the skills list; keep the
role `worker` unless you are replacing the leader):

```bash
sudo -u roundtable python3 - <<'PYEOF'
import json, os, secrets
path = "/srv/roundtable/agents.json"
with open(path, encoding="utf-8") as f:
    agents = json.load(f)
agents["agents"].append(
    {"name": "NEW-NAME", "role": "worker", "skills": ["python"],
     "token": secrets.token_urlsafe(48)}
)
with open(path, "w", encoding="utf-8") as f:
    json.dump(agents, f, indent=2)
os.chmod(path, 0o600)
print("added NEW-NAME")
PYEOF
```

**What it does:** loads the current file, appends one entry with
a fresh random token (64 characters, well over the 32-character
minimum), writes it back, and sets the file back to mode 600.
The file stays outside the git checkout, so it can never be
committed by accident.

**Expected:** `added NEW-NAME`.

**Verify — names and roles only, never the tokens:**

```bash
sudo python3 -c "import json; print([(a['name'], a['role'], a['skills']) for a in json.load(open('/srv/roundtable/agents.json'))['agents']])"
```

This prints each agent's name, role and skills — no token
anywhere. Your new agent must be in the list, and there must
still be exactly one leader. Also check the file itself:

```bash
sudo ls -l /srv/roundtable/agents.json
```

which must show `-rw-------` (mode 600) and owner `roundtable`.

**Undo:** run the same heredoc with `agents["agents"]` filtered
instead of appended (drop the entry whose name is `NEW-NAME`),
then restart (Step 3). Removing an agent deactivates it: its
claimed tasks go back to `open`, and its token stops working
after the restart. To also make sure the token can never be used
again, rotate it instead — CONNECT.md Step 16.

## Step 3 — Restart the service

The server reads `agents.json` only at startup, so the new agent
does not exist until the service restarts.

**Command:**

```bash
sudo systemctl restart roundtable
sudo systemctl is-active roundtable
```

**Expected:** `active`. If it says anything else, the config is
bad — the server fails fast at startup on a bad `agents.json`
(two leaders, a duplicate name or token, a token under 32
characters, invalid JSON). See what it complained about:

```bash
sudo journalctl -u roundtable --no-pager | tail -n 30
```

Fix the file (Step 2 again, or edit it with the same heredoc
pattern), restart again, and stop and ask if the error is still
unclear. Do not guess.

**Note:** the restart drops the leader's MCP connection — in the
leader session, open `/mcp` and choose **Reconnect** for
`roundtable` before its next board call. Muse's stored
credential stays valid across the restart (no token re-paste
needed — see the M7 notes, N1); re-paste a token only after an
actual rotation (CONNECT.md Step 16).

## Step 4 — Take the token out (one agent at a time)

Never read the whole file and never paste a token into a chat
message, a screenshot, or a git commit. Take out only the new
agent's token, by name, with no display:

- **Claude Code (leader):** the token goes into an environment
  variable, never onto the screen. In Git Bash, from the repo
  folder, **type** (do not paste):

  ```bash
  source scripts/leader-token.sh
  ```

  This loads the *leader's* token into `$ROUNDTABLE_TOKEN`
  (the script reads the `claude` entry by name). Then start the
  session with `bash scripts/start-leader.sh` — the config it
  passes carries only a reference,
  `{"Authorization": "Bearer ${ROUNDTABLE_TOKEN}"}`, which
  Claude Code expands itself, so the token is never on the
  command line. If you are adding a second leader app instead,
  adapt the script's name (`'claude'`) to the new agent's name
  first — the pattern is the same, only the name changes.
- **Muse (worker):** the token goes to the Windows clipboard,
  never onto the screen. In Git Bash, from the repo folder,
  **type**:

  ```bash
  bash scripts/copy-worker-token.sh
  ```

  This copies the *worker's* (`muse`) token with no display.
  Paste it into Muse's secure entry card — the field labelled
  **"Kunci API"** — and only there: just the token, Muse adds
  `Bearer` itself. Afterwards clear the clipboard history:
  open Win+V, choose **"Clear all"**, and then copy something
  harmless (a word from this guide) so the token is not the most
  recent item either.
- **Any other app:** use the clipboard route above for that
  agent's token (adapt the script's name from `'muse'` to the
  new agent's name), paste it once into the app's own secure
  storage, then clear the clipboard history the same way. If
  the app has no secure storage and wants the token in a config
  file, put the file outside any git checkout and set it mode
  600 — a token in a committed file is a leaked token.

If a paste ever shows bracketed-paste markers (`[200~` … `~`),
press `Ctrl+C` and type the short command instead — see
CONNECT.md Part 0. Never paste a token.

## Step 5 — Connect the app

### A generic MCP client

Any MCP client that speaks Streamable HTTP connects with three
things that never change: the URL, the header name, and the
`Bearer` scheme. The exact field names differ per client — the
shape below is the common one (Claude Code uses it with
`--mcp-config`):

```json
{"mcpServers": {"roundtable": {"type": "http", "url": "https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp", "headers": {"Authorization": "Bearer <the-agent-token>"}}}}
```

Replace `<the-agent-token>` with the token from Step 4. Keep
the token in the client's own secure storage, or in an
environment variable the config references — never in a file
you commit. If the client calls the transport something else
(`transport: streamable-http`, or just `url`), use its name for
the same three values; the board does not care what the client
calls them.

### opencode

Add the board to `opencode.json` — the global file
`~/.config/opencode/opencode.json` for every project, or an
`opencode.json` in a project root for one project:

```json
{"mcp": {"roundtable": {"type": "remote", "url": "https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp", "oauth": false, "headers": {"Authorization": "Bearer {env:ROUNDTABLE_TOKEN}"}}}}
```

- `"type": "remote"` makes opencode connect over HTTP instead
  of launching a local command.
- `"oauth": false` stops opencode from starting an OAuth flow
  on a 401. This board uses a static header, not OAuth, so
  without it a wrong token looks like a login problem instead
  of a wrong token.
- `"Bearer {env:ROUNDTABLE_TOKEN}"` reads the token from the
  environment — opencode substitutes `{env:NAME}` when it loads
  the config. Set it in the shell that launches opencode:

  ```bash
  export ROUNDTABLE_TOKEN=<the-agent-token>
  ```

  Never write the token into `opencode.json` itself. If a
  config file must hold a token, set it mode 600
  (`chmod 600 ~/.config/opencode/opencode.json`).

**Verify:** run `opencode mcp list` — it prints each configured
server and its status, so you know at once whether the board
connected, needs authentication, or failed.

### An API-only model, via worker.py

A model with an API but no agent app (for example DeepSeek, or
any OpenAI-compatible endpoint) connects through `worker.py` in
the repo root. It polls the board as a worker, claims a task the
server allows, sends it to the model, and submits the answer.
Config is by environment variables only:

```bash
ROUNDTABLE_URL=https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp \
ROUNDTABLE_TOKEN=<the-worker-token> \
WORKER_MODEL_BASE_URL=https://api.deepseek.com/v1 \
WORKER_MODEL_NAME=deepseek-chat \
WORKER_API_KEY=<the-model-api-key> \
python worker.py
```

- `ROUNDTABLE_URL` is the board; on the VM itself the loopback
  URL `http://127.0.0.1:8000/mcp` works too (the worker allows
  http only for loopback hosts, anything else must be https).
- `ROUNDTABLE_TOKEN` is the worker token from Step 4.
- `WORKER_MODEL_BASE_URL` is the model's OpenAI-compatible
  endpoint; `WORKER_MODEL_NAME` is the model name.
- `WORKER_API_KEY` is the model's API key. The token and the key
  are only ever sent in `Authorization` headers — never printed,
  never logged, never put in a task result or a message.
- The worker only calls `whoami`, `list_tasks`, `claim_task`,
  `submit_result` and `release_task`. It never creates, reviews
  or cancels tasks. Board content is untrusted: the system prompt
  tells the model the task text is data, not instructions.

The other variables keep their defaults unless you need them:
`WORKER_POLL_INTERVAL` (10 seconds),
`WORKER_MAX_TASKS_PER_HOUR` (10),
`WORKER_MODEL_TIMEOUT` (120 seconds),
`WORKER_COOLDOWN_SECONDS` (900),
`WORKER_MAX_FAILURES` (3, then the worker asks for help and
stops). Missing required variables fail fast and name the
variable (exit 2). Stop with Ctrl+C: the worker releases a
claimed task if the model call is interrupted, closes the
session and exits 0.

For an always-on worker, see "Running worker.py as a service"
below — do not just leave a terminal window open.

## Step 6 — Verify with whoami

From the new agent, call `whoami` and check the answer:

- **name** is the name you chose in Step 1,
- **role** is the role you chose,
- **skills** are the skills you chose.

If it returns another agent's name, the wrong token is
connected — stop and re-check which token you handed over in
Step 4. If the call fails with 401, the token is missing or
wrong, or the service was not restarted after Step 2. The 401
checks are the same as DEPLOY.md Step 24 (no token → 401,
wrong token → 401, correct token → 400 on an empty body).

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `whoami` returns another agent's name | The wrong token was handed over | Step 4 again, with the right agent's name |
| 401 on every call | Token missing, wrong, or the service was not restarted after the edit | Check the token (Step 4), restart (Step 3) |
| Service will not start after restart | Bad `agents.json` (two leaders, duplicate name/token, short token, bad JSON) | `journalctl -u roundtable`, fix, restart |
| Agent cannot claim a task | It lacks a required skill (M7: every required skill is needed) | Check its skills with `whoami` / `list_agents`; fix the skills (Step 2) or the task |
| SSH fails / Tailscale looks off | The connection itself is down | DEPLOY.md Part 0 / CONNECT.md Part 0 |
| A paste shows `[200~` markers | Bracketed paste, the terminal mangled it | Ctrl+C, type the short command instead (CONNECT.md Part 0) |

## Removing an agent

Removing the entry from `agents.json` (Step 2, filter instead
of append) and restarting deactivates the agent: its claimed
tasks go back to `open`. That alone does not revoke the token —
if the token may have leaked, rotate it (CONNECT.md Step 16),
which replaces it and restarts the service. Disconnecting an app
does not revoke its token either; revocation always happens on
the VM.

## Running worker.py as a service

A worker started by hand in a terminal stops when the terminal
closes. For an API-only model that should always be on, run it
as a service. The repo ships the templates in `deploy/`:
`roundtable-worker.service` (systemd unit) and
`roundtable-worker.env.example` (environment file template).

### Where it runs — the choice (Owner decision, 2026-11-14)

**Proposed default: the VM (HermesVM).** The VM is already the
operated host: systemd gives restart-on-failure and logs
(`journalctl -u roundtable-worker`), it is on 24/7, and the
worker is light — a small Python loop, tens of megabytes, far
below the board's own ~70 MiB. The unit caps it at 128 MiB
(`MemoryMax=128M`) and tells the kernel to prefer killing the
worker over the board (`OOMScoreAdjust=600`, the board is 500)
and over Hermes (0), so a runaway worker cannot eat the RAM
Hermes needs. If the board restarts, the
worker's session breaks and the worker reconnects by itself
(`run_forever` reconnects on connection errors); systemd
restarts it too if it ever exits. On the VM the worker uses the
loopback URL `http://127.0.0.1:8000/mcp` — allowed by the
worker's loopback rule, no TLS needed, and it keeps working even
if nginx or the certificate has trouble.

**Alternative: the Owner PC.** Run `worker.py` in a terminal, or
at logon via Task Scheduler, and point it at the public
`https://` URL. The model API key then stays on the Owner's own
machine and never touches the VM, and nothing at all is added
to the VM — but the worker only runs while the PC is awake, and
Windows has no systemd, so restarts and logs are manual. Choose
this when the model API key must not leave the Owner's machine.

### Installing on the VM

Over SSH, as `azureuser`:

```bash
sudo cp /srv/roundtable/app/deploy/roundtable-worker.service /etc/systemd/system/roundtable-worker.service
sudo cp /srv/roundtable/app/deploy/roundtable-worker.env.example /srv/roundtable/worker.env
sudo chown roundtable:roundtable /srv/roundtable/worker.env
sudo chmod 600 /srv/roundtable/worker.env
```

Then edit `/srv/roundtable/worker.env` and fill in the three
secrets — `ROUNDTABLE_TOKEN` (the worker token from Step 4),
`WORKER_MODEL_BASE_URL` / `WORKER_MODEL_NAME`, and
`WORKER_API_KEY` — and set `ROUNDTABLE_URL` to the loopback URL
above. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now roundtable-worker
sudo systemctl is-active roundtable-worker
sudo journalctl -u roundtable-worker --no-pager | tail -n 20
```

**Expected:** `active`, and the log shows the worker connected
as its name with its skills. Check it the same way as Step 6:
the worker's `whoami` (in the log line `connected as …`) must
show the name and skills from Step 1.

**Undo:**

```bash
sudo systemctl disable --now roundtable-worker
sudo rm /etc/systemd/system/roundtable-worker.service /srv/roundtable/worker.env
sudo systemctl daemon-reload
```

(Removing the env file deletes the API key from the VM. The
worker token itself is untouched — revoke it with CONNECT.md
Step 16 if it must die too.)

### API-key placement rules (both places)

- **Never in the repo.** `worker.env` lives in
  `/srv/roundtable/`, outside the git checkout, mode 600,
  owned by `roundtable`. The `.example` template in the repo
  carries placeholders only.
- **Never in chat.** No token, no API key, in any message,
  screenshot, or pasted log.
- **On the VM,** place the key by editing the env file over
  SSH (an editor, or a heredoc that writes the file directly),
  never in a command line that lands in shell history.
- **On the PC,** keep the key in a local `.env` file next to
  `worker.py` or in the Task Scheduler task's environment —
  never in a file you commit, and never in a shared folder.

## Sources

Every claim about the two apps in this guide comes from their
current official docs or from this repo's own verified guides,
read on 2026-11-14:

- Claude Code, remote HTTP MCP servers, custom headers, and
  `--mcp-config` / `--strict-mcp-config`:
  <https://code.claude.com/docs/en/mcp>
- opencode, MCP servers in `opencode.json` (`type: remote`,
  `url`, `headers`, `oauth: false`, `{env:NAME}`
  substitution, `opencode mcp list`): <https://opencode.ai/docs/mcp-servers/>
- This repo: DEPLOY.md Step 15 (token generation), Step 24
  (401 checks), CONNECT.md Steps 6, 14, 16 (token handoff,
  rotation), SPEC.md section 8 (worker.py), and
  `roundtable/config.py` (`load_agents_config` rules).
