# User guide

A short tour of each tab. Hover over almost anything in the app for a tooltip that explains it.

## Connecting

Enter the server address (without `/docs`) and a bearer token, then press **Connect**. The address and token are
saved in `local/settings.json`, so next time the app connects by itself. Press **Connect** again after changing
either one. A 401 message means the token was rejected.

## Explore

1. Pick a **workspace** (an isolated memory space), then a **peer** (a user, an agent, anyone Honcho remembers).
2. Type a note in the label box under the workspace list, e.g. the model that processed it. Labels appear
   everywhere and are stored on this PC only.
3. The **Processing queue** line shows background work Honcho still has to do. Wait for *idle* before judging
   or comparing anything.

Inside a peer:

- **Overview**: the peer card (short list of key facts) and the representation (Honcho's written summary).
  *About* switches to one peer's view of another.
- **Conclusions**: the individual memories.
  - *From messages* conclusions were extracted straight from what someone said.
  - *Dream* conclusions (deductive, inductive, contradiction) are produced later by dreaming.
  - Select a row and press `1` correct, `2` wrong, `3` unsure, `0` clear. The bar shows your accuracy.
  - **Find supporting messages** searches the raw messages for evidence.
  - **Snapshot** (bottom right, under the conclusions) saves the current list as a dated file: a file dialog lets you
    choose where, and nothing is ever overwritten. After a dream, **What changed?** shows what was added or removed
    since the snapshot. It compares against the snapshot you just saved or loaded, otherwise the newest one in the
    default folder. **Load snapshot…** picks an earlier snapshot of the same peer as the "before". A snapshot holds
    only that peer's conclusions, not the peer card, representation or your ratings.
  - *By* and *About* choose whose view you see. The default (the peer's own view) avoids duplicate copies.
- **Messages**: raw messages by session.
- **Ask**: Honcho's chat endpoint. Every option has a tooltip, and the preview shows the exact request that will be sent.

## Compare models

Use it when the same data was processed by different models, each in its own workspace.

1. Tick two or more workspaces.
2. Pick a peer present in all of them and press **Load / refresh**.
3. Use **Show** to switch between Counts, Peer card, Representation, Conclusions and Answers.
4. Type a question and press **Ask all** to put the same question to every workspace.

## Compare peers

Use it to ask all of one workspace's peers the same thing, for example "what do you know about me?".

1. Choose the workspace. All its peers are ticked; use **All** / **None** or tick individually.
2. Press **Load / refresh** for counts, cards, representations and conclusions, or just type a question and press **Ask all**.
3. Each peer gets a column. Answers show what that peer read (conclusions, messages, tools) when *evidence* is on.

## Compare reasoning

Use it to see what each reasoning level costs and how the answers differ for the same question.

1. Choose a workspace and a peer, and tick the levels (`minimal` to `max`). Each level is a separate question to
   Honcho, so a high level can be slow and costly.
2. Type the question and press **Ask all**. The levels run **one after another**, so their timings don't disturb
   each other. The answer streams in as it is written.
3. **Stats** shows, per level: wall time, time until the first words, and what the agent read (conclusions,
   messages, tool calls) from the evidence. **Answers** shows the text.
4. For **real token counts, server time, iterations and models**, start the telemetry listener (left panel) and point
   Honcho's `TELEMETRY_ENDPOINT` at it. Setup and troubleshooting are in [telemetry.md](telemetry.md).
   Until the event arrives the cells say `waiting…`, and `listener off` if it isn't running.

## Monitor

Use it to watch many runs at once, for example while a script extracts messages in a dozen workspaces, then dreams,
then asks questions. It needs the telemetry listener (left panel): press **Start listening** and point Honcho's
`TELEMETRY_ENDPOINT` at it, as described in [telemetry.md](telemetry.md). Nothing else to set up.

- **Rows appear by themselves** when a workspace sends its first event. **Watch** and **Watch all** add workspaces
  that haven't sent anything yet, so their queue still shows.
- **State**: *active* (heard in the last 15 s), *stalled* (work is waiting in the queue but nothing has been heard for
  longer than **Stalled after**, 3 minutes by default), *done* (the queue is empty) or *idle*. Stalled rows are tinted
  red and sort to the top. A very long single model call is silent until it finishes, so raise the limit if you see
  false alarms.
- **Last event** says how long ago and what it was doing (extracting, dreaming, questions).
- **Queue** is Honcho's own work queue for the workspace (done / total, running, waiting), polled every 10 seconds
  with a read-only call. Untick **Poll queue status** to stop polling.
- **Extraction**, **Dreaming** and **Questions** count batches of messages turned into conclusions, dream runs with the
  conclusions created (+) and deleted (−), and questions answered. **LLM calls** and **Tokens** add up the model
  calls. **Problems** lists failed calls, retries, fallbacks to another model, and failed observers or specialists.
- **Show** filters the table: *Working now* (the default: active, stalled, work waiting or running in its queue, or heard
  from in the last 10 minutes), *Active*, *Needs attention* (stalled, or any failed call, retry, fallback or failed
  observer), *Finished* or *Everything*. The box next to it filters by name, and several words separated by commas
  match any of them (for example `test, prod`). A label says how many of the rows are showing.
- Select several rows (Ctrl or Shift click) to see them together: a summary per workspace with totals, and one live
  feed merged from all of them. **Only selected** hides every other row until you click it again.
- **Drag a column header** to rearrange the columns; the order is remembered. Right-click a header and choose
  *Reset column order* to go back to the default.
- Click a column header to sort (numbers sort as numbers). Click a row to see its model calls by purpose, recent
  problems and a live feed of its events, newest first.
- **Clear** empties the table. It doesn't touch the log on disk.

Honcho can *sample* its high-volume events (a server setting). If yours does, the call and token totals undercount.

## Rearranging the columns

On the three Compare tabs every column title has a **◀** and a **▶**. Click one to move that column a step to the
left or right, so any two columns can sit side by side without closing the others. The order applies to every view
and is kept when you ask again and when you save results (a page you open again keeps its order too). In the
Monitor, drag a column header instead.

## Saving a comparison

**Save results** on any Compare tab asks where to save. It suggests `local/comparisons/<date-time>_<name>.html`
(and next time starts in the folder you last used). The page holds the question, the table, and for each column the
answer with its evidence, peer card, representation and conclusions. On **Compare reasoning** it also holds the
telemetry: the tokens, server time and iterations, plus every event behind each run. It's an ordinary HTML page you
can open in any browser. The reports contain real memory content, so keep them private.

**Open saved results…** (same tab) loads such a page back and shows it exactly as it was, with every view and number,
without asking Honcho anything. It works without a server connection. Pages saved from one kind of tab open in that
tab. Asking a question or pressing Load / refresh switches back to live results.

## Where things are saved

See "Your data stays on your PC" in the [README](../README.md).
