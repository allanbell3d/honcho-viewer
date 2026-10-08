# Live telemetry: real token counts

Honcho's chat answer contains only the text (and, if asked, the evidence). The **numbers** (tokens, server time,
iterations, which models ran) are in Honcho's *telemetry*: a `dialectic.completed` event that the server posts for
every question. The **Compare reasoning** tab can receive those events live and put them next to each answer, and the **Monitor** tab uses them to show every workspace live.

The viewer only *listens*. It never talks back to Honcho and never writes to its database.

## Setup

1. In the **Compare reasoning** or **Monitor** tab press **Start listening**. Choose a port if the default is taken.
2. Press **Copy Honcho settings** and put the lines in Honcho's environment, then restart Honcho:

   ```
   TELEMETRY_ENABLED=true
   TELEMETRY_ENDPOINT=http://<this-pc>:8099/
   TELEMETRY_HEADERS={"X-Telemetry-Key": "<your secret>"}     # only if you set a secret
   ```

3. Ask a question. Within a second or two the Stats table fills in.

Notes:
- If Honcho runs in Docker or on another machine, `<this-pc>` must be an address *that machine* can reach. That is
  the address the viewer shows. Windows Firewall asks for permission the first time.
- The listener accepts posts on all network interfaces (`0.0.0.0`). **Only accept from** and **Secret** are optional
  ways to lock it down, and both are saved in your local settings. Leave them empty unless you need them.
  **Only accept from** limits who may post, by sender IP address. If Honcho runs in Docker, its posts may arrive from
  the Docker gateway address (for example `172.17.0.1`) instead of the machine's own address, so check the 403 note
  below if events don't arrive. **Secret** is a shared key that Honcho sends in the `X-Telemetry-Key` header.
- Honcho only sends events while `TELEMETRY_ENABLED` is true. Per Honcho's own settings notes, aggregate events such
  as `dialectic.completed` are never sampled, so the totals are reliable.

## One listener, several tabs

The listener is shared. Compare reasoning uses it to match each answer to its event, and the **Monitor** tab uses
the same events to show every workspace live. Starting or stopping it in one tab starts or stops it for all of them.
Open the Monitor tab to see all workspaces at once.

## What is saved

The events the viewer receives are also written to `local/telemetry/events-YYYYMMDD.jsonl` (one file per UTC day).
The oldest day is deleted when the folder passes **1 GB**; change that with `telemetry_cap_mb` under `"ui"` in
`local/settings.json` (0 = no limit).

If your Honcho has `TELEMETRY_TRACE_PAYLOADS_ENABLED` on, the events contain full prompts and answers, which means
real memory content. `local/` is git-ignored; keep it that way and don't share the folder.

## How an answer is matched to its event

By **workspace + peer + reasoning level + arrival time** (the first matching event after the question was sent).
The levels are asked one after another so this is unambiguous. If someone else asks the same peer at the same level
during your test, the match can be wrong, so run comparisons when nobody else is chatting with that peer.

## Troubleshooting

- **"listener off"**: press Start listening.
- **"waiting…" then "not received"**: Honcho is not reaching the viewer. Open `http://<this-pc>:8099/` from the
  Honcho machine (it answers with one line), check the firewall and that Honcho was restarted with
  `TELEMETRY_ENABLED=true`.
- **401 in Honcho's log**: the secret in `TELEMETRY_HEADERS` does not match the viewer's.
- **403 in Honcho's log**: the sender's address is not in **Only accept from**. Clear the field, or add the address
  Honcho's posts actually come from (with Docker, often the gateway address rather than the host's).
- **"Cannot listen on port …"**: another program, or another copy of the viewer, is already using that port. Close it
  or choose another port. (The listener never shares a port, so two copies can't both claim it.)
