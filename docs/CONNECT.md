# Connecting two real agent apps — Roundtable M4

This guide connects the two apps that will use the board on HermesVM:

- **Leader: Claude Code** — a dedicated session on the Owner's computer,
  in an empty folder, with permission prompts on.
- **Worker: Muse** (this app) — connected from Muse's side, no software
  to install on the Owner's computer.

The board itself does not change in M4. The server is the deployed
commit `42106be` (see `docs/audits/AUDIT-M3-DEPLOY.md`), at
`https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp`.

Read `docs/DEPLOY.md` first if you have not run it: the steps below
reuse its Step 15 (token generation), Step 25 (token on stdin) and
Step 43 (backup). The same rules apply throughout: one command at a
time, each approved by the Owner; Hermes is never stopped, restarted
or reconfigured; nothing Roundtable did not create is touched.

**What you need before Step 1:** an SSH session to the VM over
Tailscale (as in DEPLOY.md), the Auditor present, and the two new
agent names decided (Step 1 uses them). The Owner's computer is
Windows; run the PC-side commands in **Git Bash** (a POSIX shell),
not PowerShell 5.1, which aliases `curl` to `Invoke-WebRequest`
(see DEPLOY.md Part 11).

## Sources

Every claim about the two apps comes from their current official
docs, read on 2026-11-11:

- Claude Code, remote HTTP MCP servers, custom headers, scopes and
  environment-variable expansion:
  <https://code.claude.com/docs/en/mcp>
- Claude Code, permission modes and the permission table:
  <https://code.claude.com/docs/en/permissions>
- Claude Code, first-server walkthrough:
  <https://code.claude.com/docs/en/mcp-quickstart>
- Muse: the app's own custom-connector documentation (the
  MCP client template sends the credential in a header, as a bearer
  token or a custom header; the credential lives in the registered
  connection, never in a file; credentials are collected through the
  secure entry flow, never in chat). That documentation is the
  in-product skill, a local file, not a web page, so it **cannot be
  linked by URL**. Meta's Help Center describes Custom Connectors in
  general — you ask Muse to create one, credentials go to its Secure
  Credentials Store, Meta does not review custom connectors — but it
  does not document MCP for the app. The plain-language wording to
  ask Muse with follows the published connector guides for the same
  flow (CData, LaunchList, SealGate), which describe asking Muse to
  build a custom connector and entering the credential only in its
  secure prompt.

## Part 1 — Rename the agents on the VM

The VM currently has `planner` (leader) and `coder` (worker) from
DEPLOY.md Step 15. They are replaced by `claude` (leader, skills
`planning`, `review`) and `muse` (worker, skills `python`,
`writing`). `python` is the skill the M4 demo task needs
(see `docs/DEMO.md`); `writing` is what the worker does in the
demo — it writes code as text, it runs nothing.

### Step 1 — Back up the database first

**Command:**

```bash
sudo -u roundtable sqlite3 /srv/roundtable/data/roundtable.db ".backup /srv/roundtable/backups/roundtable-$(date +%F).db"
sudo chmod 600 /srv/roundtable/backups/roundtable-"$(date +%F)".db
```

**What it does:** the same backup as DEPLOY.md Step 43. Do this
before any change to the agents: if the rename goes wrong, the
board can be restored from here.

**Expected:** no output from the first command; the backup file
exists afterwards (`sudo -u roundtable ls -l
/srv/roundtable/backups/` shows it, mode `-rw-------`).

**Undo:** delete the backup file (see Step 43).

### Step 2 — Confirm the old leader token still works

**Command:**

```bash
TOKEN=$(sudo python3 -c "import json; d={a['name']: a['token'] for a in json.load(open('/srv/roundtable/agents.json'))['agents']}; print(d['planner'])")
printf 'Authorization: Bearer %s\n' "$TOKEN" | curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/mcp -H @- -H "Content-Type: application/json" -d '{}'
```

**What it does:** reads the current leader token (`planner`) into a
shell variable — selected by agent name, not by its position in the
file — and sends it to the app the same way DEPLOY.md Step 25 does.
The token never enters shell history. Keep this shell open: the
same `$TOKEN` is used again in Step 5 to prove the old token is
dead.

**Expected:** `400`. That is correct: the token is accepted, and
the empty request body is what the app rejects.

**Undo:** nothing to undo (read-only check). Do **not** `unset
TOKEN` yet.

### Step 3 — Write the new agents file (tokens generated inside Python)

**Command:**

```bash
sudo cp /srv/roundtable/agents.json /srv/roundtable/agents.json.bak
sudo chmod 600 /srv/roundtable/agents.json.bak
sudo -u roundtable python3 - <<'PYEOF'
import json, os, secrets
agents = {"agents": [
    {"name": "claude", "role": "leader", "skills": ["planning", "review"],
     "token": secrets.token_urlsafe(48)},
    {"name": "muse", "role": "worker", "skills": ["python", "writing"],
     "token": secrets.token_urlsafe(48)},
]}
path = "/srv/roundtable/agents.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(agents, f, indent=2)
os.chmod(path, 0o600)
print("wrote", path)
PYEOF
```

**What it does:** first keeps a mode-600 copy of the old file
(`agents.json.bak`) — it is deleted in Step 5, once the old token
is proven dead. Then, exactly the DEPLOY.md Step 15 pattern, with
the two new names. The tokens are generated inside Python and
written straight to the file — they never appear in shell history
and you never type them. The file stays mode 600, owned by
`roundtable`, outside the git checkout, so it can never be
committed by accident. There is exactly one leader, the tokens are
64 characters (well over the 32-character minimum), and no two
agents share a token.

**Expected:** `wrote /srv/roundtable/agents.json`. Verify the names
and roles — without ever printing a token — with:

```bash
sudo python3 -c "import json; d=json.load(open('/srv/roundtable/agents.json')); print([(a['name'], a['role'], a['skills']) for a in d['agents']])"
```

which must print
`[('claude', 'leader', ['planning', 'review']), ('muse', 'worker', ['python', 'writing'])]`.

> **Note:** renaming the file does not remove the old agents from
> the database. `planner` and `coder` stay there as **inactive**
> rows (removed agents are deactivated, and any tasks they had
> claimed reopen — the M1 behaviour). So `list_agents`, which lists
> active agents only, will show just `claude` and `muse`. That is
> expected, not a problem.

**Undo:** restore the backup and restart (Step 4):

```bash
sudo cp /srv/roundtable/agents.json.bak /srv/roundtable/agents.json
sudo systemctl restart roundtable
```

(This restores the old tokens. Every agent using the new ones stops
working until you generate new ones again.)

### Step 4 — Restart the service so the new file loads

**Command:**

```bash
sudo systemctl restart roundtable
sudo systemctl is-active roundtable
```

**What it does:** the app reads `agents.json` once at startup, so
a restart is the only way the new agents take effect. This restarts
only Roundtable; Hermes is a separate user-level service and is not
touched.

**Expected:** `active`. If it is not active, stop and read the log
(`sudo journalctl -u roundtable -n 30 --no-pager`) before doing
anything else.

**Undo:** re-running Step 3 with the old file and restarting again
restores the old agents.

### Step 5 — Prove the old token is dead (401)

**Command:**

```bash
printf 'Authorization: Bearer %s\n' "$TOKEN" | curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/mcp -H @- -H "Content-Type: application/json" -d '{}'
unset TOKEN
```

**What it does:** sends the **old** leader token from Step 2 to the
restarted app. It must now be rejected, because `planner` no longer
exists. Then the variable is cleared.

**Expected:** `401`. If you get `400`, the old file is still
loaded: check that Step 3 printed `wrote
/srv/roundtable/agents.json` and that Step 4 restarted the service,
then stop and ask the Auditor.

Now that the old token is proven dead, delete the backup of the old
file — it holds the old tokens, and they must not linger on the VM:

```bash
sudo rm /srv/roundtable/agents.json.bak
```

**Undo:** nothing to undo (read-only check).

## Part 2 — Taking ONE token out of the VM safely

A token has to travel from the VM to exactly one app on the Owner's
computer. The rules for that trip:

- **No chat.** Never paste a token into any chat, message, email or
  shared document — including a chat with Muse. Muse will never ask
  for it in chat; it asks through its secure entry card instead.
- **No screenshot.** Never photograph or screenshot a token.
- **No shell history.** The token is never typed as a command, so it
  never lands in any history file.
- **No file left behind.** No file on the VM and no file on the
  Owner's computer may contain a token after the handoff.

Do the tokens **one at a time**: take the leader token out, connect
Claude Code (Part 3), verify it, and only then take the worker token
out for Muse (Part 4). That way a mistake affects one app, not both.

### Step 6 — Show one token, copy it, clear the screen

**The Owner runs this step in their own SSH window — never through
an AI agent's tool.** The step prints a token; if an agent (the
Auditor's session, or any other) ran it, the token would land in
that agent's transcript, which breaks "no chat" just as surely as
pasting it into a chat yourself.

Run this in the SSH session, for **one** agent at a time. For the
leader (`claude`):

**Command:**

```bash
TOKEN=$(sudo python3 -c "import json; d={a['name']: a['token'] for a in json.load(open('/srv/roundtable/agents.json'))['agents']}; print(d['claude'])")
printf '%s\n' "$TOKEN"
unset TOKEN
```

For the worker (`muse`), change `'claude'` to `'muse'` and run it
as a separate step, after the leader is connected and verified.
Tokens are selected by agent name, never by their position in the
file.

**What it does:** reads the token into a shell variable and prints
it once. The token is never typed, so it never enters shell history,
and nothing is written to disk.

**How to copy it:** select the printed token with the mouse in your
terminal (Windows Terminal: drag to select, right-click to copy)
and paste it **directly** into the app's prompt — the `read -s`
prompt for Claude Code (Step 10), or Muse's secure entry card
(Step 14). Paste it nowhere else, not even into a text editor "for
a moment".

**Then, immediately:**

```bash
clear
```

**Why `clear` matters:** the token stays in the terminal's
scrollback until the window is closed. Clearing removes it from
view; **close the SSH window when you are done** to clear the
scrollback itself. Do not save the terminal output to a file.

**If you copy the token, clear the clipboard history too.**
Windows clipboard history (Win+V) keeps copies of everything you
copy. After pasting the token into the app, open Win+V, choose
"Clear all", and then copy something harmless (a word from this
guide) so the token is not the most recent item either.

**Prefer no display at all for Claude Code.** The leader token
never needs to appear on screen: in Git Bash on the Owner's
computer, run the token straight from the VM into the shell
variable, using Windows OpenSSH (Git Bash's own `ssh` is refused
by this VM — use `ssh.exe`):

```bash
read -r ROUNDTABLE_TOKEN < <(/c/Windows/System32/OpenSSH/ssh.exe azureuser@<vm-host> "sudo python3 -c \"import json; d={a['name']: a['token'] for a in json.load(open('/srv/roundtable/agents.json'))['agents']}; print(d['claude'])\"")
export ROUNDTABLE_TOKEN
```

`<vm-host>` is the same host you SSH to for the VM steps. `read -r`
reads the token from the SSH output without echoing it, so it is
never displayed, never typed, and never written to a file. Use
this route for the leader, and keep the display-and-copy route
above for Muse (its secure entry card needs a paste) and as the
fallback if this one fails.

**Where each app stores it afterwards:**

- **Claude Code:** with the route in Part 3, the token is stored
  **nowhere on disk**. Only the text `${ROUNDTABLE_TOKEN}` is
  written into `~/.claude.json`, under the `~/roundtable-leader`
  project entry (local scope — see Step 9); the value itself lives
  only in the shell session that starts Claude Code, set with
  `read -s` in Step 10 or with the no-display route above. Close
  that shell and the value is gone. (If you ever use the fallback
  in Step 11 instead, the literal token is stored in
  `~/.claude.json` — see that step.)
- **Muse:** the token is stored in Muse's Secure Credentials Store,
  under the registered Roundtable connection. It is never written to
  a file — not in the connector doc, not in a skill, not on the
  Owner's computer. Muse's own connector documentation says the
  credential lives in the registered connection, never in a file.

## Part 3 — Connecting Claude Code as the leader

Claude Code's own docs
(<https://code.claude.com/docs/en/mcp>) say HTTP servers are the
recommended option for remote MCP servers, and that a token header
is passed with `--header`. They also say Claude Code expands
environment variables in a server's `headers` value
(`"Authorization": "Bearer ${ROUNDTABLE_TOKEN}"`). What the docs
do **not** say is whether a header value is shown by name (as
`${ROUNDTABLE_TOKEN}`) or by its expanded value in `claude mcp
get` output — they say that only for the URL and command line.
Step 9 checks that with a dummy value first, before any real token
is set. A variable name of your own (here `ROUNDTABLE_TOKEN`)
expands as written; do not use one of Claude Code's own credential
names (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, …), which it
reads as empty in a remote server's `headers`.

**Scope is local, not user.** A local-scoped server loads only in
the project where you added it — here, only in
`~/roundtable-leader`. A user-scoped server would load in *every*
Claude Code session on the PC, including the Auditor's own session
and any unrelated project, which would give every one of those
sessions leader access to the board. That is exactly what the
separation of leader and Auditor is meant to prevent. **The
Auditor's own Claude Code session must not have the `roundtable`
server** — if it ever does, remove it with `claude mcp remove
roundtable --scope local` from that session's folder.

### Step 7 — Make an empty folder for the leader session

On the Owner's computer, in Git Bash:

**Command:**

```bash
mkdir -p ~/roundtable-leader
cd ~/roundtable-leader
ls -la
```

**What it does:** creates the folder the leader session will run
in, and moves into it.

**Why empty:** Claude Code can read and (with your approval) write
files in its working directory, and it reads the files there into
its context. An empty folder means there is nothing for it to read
by accident, nothing to overwrite, and no repository whose settings
could change how it behaves. The board is the leader's whole world
in this demo.

**Expected:** `ls -la` shows only `.` and `..` — no files.

**Undo:** `cd ~ && rm -rf ~/roundtable-leader` (only after the
session is closed).

### Step 8 — Install Claude Code, if it is not installed yet

Follow the official install instructions on the Claude Code docs
site (the MCP quickstart,
<https://code.claude.com/docs/en/mcp-quickstart>, is the
first-server walkthrough). Do not install it from anywhere else.
Then come back here.

### Step 9 — Add the board, and check what `claude mcp get` prints (dummy value first)

In the same Git Bash window, in `~/roundtable-leader`:

**Command:**

```bash
ROUNDTABLE_TOKEN=dummy-check claude mcp add-json roundtable --scope local '{"type":"http","url":"https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp","headers":{"Authorization":"Bearer ${ROUNDTABLE_TOKEN}"}}'
ROUNDTABLE_TOKEN=dummy-check claude mcp get roundtable
```

**What it does:** registers the board under the name `roundtable`
in **local scope** — stored in `~/.claude.json` under this
project's entry, loading only in `~/roundtable-leader`, private to
you, never in a project file. (Local scope is the default; the
flag is written out so the scope is a deliberate choice, not an
accident. Project scope is never used here: it would write a
`.mcp.json` into a project folder.) The single quotes matter: they
stop the shell expanding `${ROUNDTABLE_TOKEN}`, so the config file
holds the reference, not the token. Claude Code expands it from
the environment each time it connects.

The `ROUNDTABLE_TOKEN=dummy-check` prefix sets the variable to a
dummy value for those two commands only. That is the point of this
step: the docs say Claude Code shows `${VAR}` references by name
for a server's URL and command line, but they do **not** say that
for header values. With a dummy value you can see what `get`
actually prints, before any real token is involved. Look at the
`get` output now:

- If the Authorization header shows as
  `${ROUNDTABLE_TOKEN}`, the header is shown by name — `get` is
  safe to use with the real token (Step 11).
- If it shows `dummy-check`, the header is shown by its expanded
  value — **do not run `get` with the real token**. Use
  `claude mcp list` only (Step 11).

**Expected:** a line like
`Added HTTP MCP server roundtable with URL:
https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp to local
config`, then the `get` output. The server will show as failed to
connect — expected, the dummy value is not a real token. If the
command complains about its flags, run
`claude mcp add-json --help` and check the flag order — the command
and its `--scope` flag are documented at
<https://code.claude.com/docs/en/mcp>.

**Undo:**

```bash
claude mcp remove roundtable --scope local
```

### Step 10 — Give this shell the leader token (never typed, never stored)

In the same Git Bash window, in `~/roundtable-leader`:

**Command:**

```bash
read -s ROUNDTABLE_TOKEN
export ROUNDTABLE_TOKEN
```

**What it does:** `read -s` waits for you to paste the leader
token (from Step 6) without echoing it to the screen, and stores it
in the shell variable `ROUNDTABLE_TOKEN`. `export` makes it visible
to programs this shell starts — including Claude Code. The value is
never written to a file and never enters shell history; it lives
only in this shell session. (If you used Step 6's no-display route
instead, the variable is already set — skip the `read -s` line and
just run `export ROUNDTABLE_TOKEN`.)

**Expected:** no output. The cursor sits still while you paste —
that is `read -s` hiding the input, not a frozen terminal. Press
Enter after pasting.

**Undo:** `unset ROUNDTABLE_TOKEN` (and close the shell). Note: if
you open a **new** Git Bash window later, the variable is gone and
you must do this step again before starting Claude Code — that is
by design.

### Step 11 — Check the connection (and the fallback if it fails)

**Command:**

```bash
claude mcp list
```

and, **only if Step 9 showed the header as
`${ROUNDTABLE_TOKEN}`** (shown by name, not by value):

```bash
claude mcp get roundtable
```

**What it does:** lists the server's health. A connected server
shows `✔ Connected`, with 11 tools. Only use `get` with the real
token if Step 9 proved the header is shown by name — otherwise
`get` could print the token, and `list` is all you need.

**Expected:** `roundtable` shows `✔ Connected`, with 11 tools.
If it shows `✘ Failed to connect`, first check the variable is
actually set in **this** shell:

```bash
echo ${ROUNDTABLE_TOKEN:+set}
```

which must print `set` (it prints the word `set`, never the token).
If it prints nothing, you are in a different shell than Step 10 —
go back to Step 10 in this window. If the variable is set and the
server still fails with a 401, the header is not reaching the board:
check that the URL in Step 9 has no trailing slash and no typo,
then stop and ask the Auditor.

**Fallback (only if the `${VAR}` route does not work):** add the
server with the header instead, letting the shell expand the
variable once:

```bash
claude mcp add --transport http roundtable --scope local https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp --header "Authorization: Bearer ${ROUNDTABLE_TOKEN}"
```

This stores the **literal token** in `~/.claude.json` (the shell
expands `${ROUNDTABLE_TOKEN}` before Claude Code saves it). It
works, but the token is then on disk in `~/.claude.json`: remove
the server with `claude mcp remove roundtable --scope local` when
you are done, and rotate the token on the VM (Part 5) if the
computer is ever shared or lost. Do not use this route unless Step
9's route has actually failed.

### Step 12 — Start the leader session, prompts on

In the same Git Bash window, in `~/roundtable-leader`:

**Command:**

```bash
claude
```

**What it does:** starts Claude Code in the empty folder, in its
default permission mode. In that mode Claude Code prompts for
permission on first use of each tool — shell commands, file edits,
web fetches, and MCP tool calls all ask first (see the permission
table at <https://code.claude.com/docs/en/permissions>). That is
what you want: the board's content is untrusted — task text,
results and messages are data written by other agents, and must
never be able to make the leader run something you did not approve.

**Never** start the leader with `--dangerously-skip-permissions`
(or `bypassPermissions` mode): it skips every permission prompt,
which is the opposite of what this demo needs. Also do not answer
"Yes, and don't ask again" for a board tool call — approve each one
as it comes, so you see what the leader is about to do.

**Expected:** the Claude Code welcome screen, in
`~/roundtable-leader`. Check the mode: press `/permissions` (or
type it) and confirm there is no allow rule for `Bash`, `Edit`,
`Write` or `mcp__roundtable__*`. If there is one from an earlier
session, remove it before continuing.

**Undo:** `Ctrl+C` twice, or type `/exit`, to end the session.

### Step 13 — Verify the leader: whoami, then the 401 checks

In the leader session, ask:

> Call the `whoami` tool on the roundtable server and tell me the
> name, role and skills it returns.

**Expected:** name `claude`, role `leader`, skills `planning`,
`review`. If it returns anything else, stop — the wrong token is
connected.

Then, in Git Bash (outside the session), the two negative checks.
They need no real token — the second uses a deliberately wrong one:

```bash
PUBLIC_NAME=roundtable-k7q2.eastasia.cloudapp.azure.com
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://$PUBLIC_NAME/mcp -H "Content-Type: application/json" -d '{}'
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://$PUBLIC_NAME/mcp -H "Authorization: Bearer wrong-token" -H "Content-Type: application/json" -d '{}'
```

**Expected:** `401` and `401` — a missing token and a wrong token
are both rejected before the app processes anything. (A correct
token would return `400` here, as in Step 2 — the empty body is
rejected, not the token.)

## Part 4 — Connecting Muse as the worker

Muse has no "add MCP server" form to fill in. Instead you ask Muse
in plain language to build a custom connector for the board, and it
builds one on its secure VM using the official MCP SDK, speaking
Streamable HTTP and sending the credential in an `Authorization:
Bearer` header. The credential is collected through Muse's secure
entry flow and stored in its Secure Credentials Store under the
registered Roundtable connection — never in a file, never in the
chat. That is Muse's own documented custom-connector flow; the
wording below follows the published connector guides for the same
flow.

### Step 14 — Ask Muse to build the connector (main chat)

In the main chat with Muse (this app), send exactly:

> Create a custom connector for a remote MCP server, then connect
> to it:
>
> Name: Roundtable
> Transport: remote streamable HTTP
> URL: https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp
> Auth: Authorization header (Bearer token)
>
> Ask me for the token in your secure credentials prompt, never in
> the chat. Save the connector so it is available in future chats.

**What happens next:** Muse sets up the connection and shows a
secure entry card for the credential. Paste the **worker** token
(from Step 6, `[1]`) into that card — and only there. If the card
asks for the Authorization header value, paste
`Bearer <the token>`; if it asks for the token, paste just the
token. Muse never asks for the token in the chat itself; if any
message asks you to type it in the chat, stop — that is not how
this works.

**Expected:** Muse confirms the connector, lists the board's tools
(11 of them, including `whoami`, `list_tasks`, `claim_task`,
`post_message`, `submit_result`, `review_task`), and runs a first
harmless read to prove it works. If Muse reports that it cannot
send a static `Authorization: Bearer` header, **stop and ask the
Owner** — do not add an OAuth server or any other auth change to
Roundtable in M4. (Muse's own connector documentation says its MCP
client sends the credential in a header as a bearer token, so this
is not expected to happen.)

**Undo:** ask Muse to remove the Roundtable connector, or remove
it under Settings → Connectors in the Muse app. Removing it on
Muse's side does not revoke the token — see Part 5.

### Step 15 — Verify the worker: whoami, then the 401 checks

In the chat with Muse, ask:

> Call `whoami` on the Roundtable board and tell me the name, role
> and skills it returns.

**Expected:** name `muse`, role `worker`, skills `python`,
`writing`. If it returns anything else, stop — the wrong token is
connected.

The 401 checks are the same two `curl` commands as Step 13 (no
real token needed) — run them once, they cover the board, not the
app. If you already ran them in Step 13, do not repeat them.

## Part 5 — Revoking a token and disconnecting an app

Removing a server from an app does **not** revoke its token. The
token stays valid until it is replaced on the VM. So there are two
separate actions, and they do different things:

- **Disconnect** an app: the app stops calling the board, but its
  old token would still work if someone had a copy.
- **Revoke** (rotate) a token: the old token stops working
  everywhere, at once. Do this whenever a token may have leaked, a
  computer is lost, or an app is removed for good.

### Step 16 — Rotate one agent's token (revoke)

Back up first (Step 1). For the worker (`muse`):

**Command — read the old token into `$TOKEN` first:**

```bash
TOKEN=$(sudo python3 -c "import json; d={a['name']: a['token'] for a in json.load(open('/srv/roundtable/agents.json'))['agents']}; print(d['muse'])")
```

**Command — then rotate and restart:**

```bash
sudo -u roundtable python3 - <<'PYEOF'
import json, os, secrets
path = "/srv/roundtable/agents.json"
with open(path, encoding="utf-8") as f:
    agents = json.load(f)
for a in agents["agents"]:
    if a["name"] == "muse":
        a["token"] = secrets.token_urlsafe(48)
with open(path, "w", encoding="utf-8") as f:
    json.dump(agents, f, indent=2)
os.chmod(path, 0o600)
print("rotated muse")
PYEOF
sudo systemctl restart roundtable
```

**Command — then prove the old token is dead:**

```bash
printf 'Authorization: Bearer %s\n' "$TOKEN" | curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/mcp -H @- -H "Content-Type: application/json" -d '{}'
unset TOKEN
```

**What it does:** the order matters. The old token is read **before**
the rotation — after the rotation the file holds the new token, so
reading it then would test nothing. The rotate step loads the
current file, replaces only `muse`'s token with a fresh random one,
writes it back mode 600, and restarts the service so the new token
loads. The leader's token is untouched, so Claude Code keeps
working. Tokens are selected by agent name throughout, never by
their position in the file.

**Expected:** `rotated muse`, then `active` from the restart
(`sudo systemctl is-active roundtable`), then `401` for the old
token. If the old token returns `400`, the rotation did not take
effect — check the file and restart again, then stop and ask the
Auditor. Then take the new token out (Step 6, `'muse'`) and
re-connect Muse (Step 14) — the old connector's stored token no
longer works.

**Undo:** rotating is one-way: there is no way back to the old
token. The undo for a mistaken rotation is to rotate again and
re-connect the app with the new token.

### Step 17 — Disconnect Claude Code

In Git Bash, in `~/roundtable-leader`:

**Command:**

```bash
claude mcp remove roundtable --scope local
unset ROUNDTABLE_TOKEN
```

**What it does:** removes the server from Claude Code's local
config for this folder, so no session started there will connect to
the board again; then clears the token from this shell. Close the
shell afterwards. (Remove it from this folder because that is where
it was added — local scope lives under the project entry.)

Note: `claude mcp logout roundtable` is for OAuth servers — it
revokes a stored OAuth token. This board uses a static header, so
there is no OAuth token to log out; revocation is Step 16, on the
VM. Removing the server alone does not revoke the token.

**Expected:** a confirmation that the server was removed, and
`claude mcp list` no longer shows `roundtable`.

### Step 18 — Disconnect Muse

Ask Muse to remove the Roundtable connector, or remove it under
Settings → Connectors in the Muse app. The stored credential is
removed with the connection. As with Claude Code, this alone does
not revoke the token — rotate it (Step 16) if it may have leaked
or if the worker is gone for good.

After both apps are disconnected, the board keeps running on the
VM with its agents in `agents.json`. Removing an agent from the
file entirely is a separate operation (it deactivates the agent and
reopens any tasks it had claimed); it is not needed just to
disconnect an app, and it is not part of M4.
