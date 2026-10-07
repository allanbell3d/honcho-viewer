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
  - **Snapshot** saves the current list; after a dream, **What changed?** shows what was added, removed or edited.
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

## Saving a comparison

**Save results** on either Compare tab writes `local/comparisons/<date-time>_<name>.html`: the question, the counts
table, and for each column the answer, peer card, representation and conclusions. Saves never overwrite each other.
The reports contain real memory content, so keep them private.

## Where things are saved

See "Your data stays on your PC" in the [README](../README.md).
