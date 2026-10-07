"""Plain-English explanations shown as hints and tooltips (the 'tutorial' layer)."""

LEVELS = {
    "explicit": ("From messages",
                 "Explicit: a fact Honcho extracted directly from what someone said.\n"
                 "Created soon after messages arrive (the 'deriver')."),
    "deductive": ("Dream · deduction",
                  "Deductive: a new fact logically implied by other conclusions.\n"
                  "Created while 'dreaming' (background consolidation). Its premises are listed."),
    "inductive": ("Dream · pattern",
                  "Inductive: a generalisation from several conclusions (a pattern or habit).\n"
                  "Created while dreaming. Its supporting conclusions are listed."),
    "contradiction": ("Dream · conflict",
                      "Contradiction: dreaming found conclusions that disagree with each other.\n"
                      "The conflicting conclusions are listed."),
}

TAB_HINTS = {
    "overview": "The peer card is a short list of key facts. The representation is an excerpt of Honcho's "
                "conclusions about this peer (up to 100, newest first); the Conclusions tab has all of them.",
    "conclusions": "Conclusions are the individual 'memories'. Explicit ones come straight from messages; "
                   "deductive, inductive and contradiction ones are produced later by dreaming. "
                   "Rate them with 1 / 2 / 3 to measure accuracy.",
    "messages": "The raw conversation Honcho received. Everything else is derived from this.",
    "ask": "Ask Honcho a question about this peer (the 'dialectic' API). It searches its memory, "
           "reasons, and answers. Nothing is stored. Hover any option to learn what it does.",
    "compare": "Pick workspaces that processed the same data with different models, then compare "
               "what each one built, side by side.",
    "compare_peers": "Pick one workspace and tick several of its peers (agents, users), then ask them all the same "
                     "question, e.g. 'what do you know about me?', and read the answers side by side.",
}

ASK_OPTIONS = {
    "query": "query: your question, in plain language. Honcho answers it from what it knows about the peer.",
    "reasoning_level": "reasoning_level (default: low)\nHow hard Honcho thinks before answering:\n"
                       "minimal / low are fast and cheap; medium / high / max search and reason more "
                       "(slower, more tokens, usually better on tricky questions).",
    "include_evidence": "include_evidence (API default: off, on here)\nAlso return what Honcho READ while "
                        "answering: the conclusions, the messages and the tools it used. It lists what was "
                        "accessed, which is not necessarily what the answer relied on.",
    "target": "target (default: none)\nAnswer from this peer's point of view ABOUT another peer, e.g. "
              "'what does agent know about alice?'. Leave on 'itself' to ask about the peer directly.",
    "session_id": "session_id (default: none = all sessions)\nOnly use memories from one conversation session.",
    "scope": "scope (default: none)\nAdvanced: confine recall to named scope(s). Comma-separate for several. "
             "Can't be combined with a session. Needs a workspace- or admin-level key.",
    "response_format": "response_format (default: none)\nAdvanced: a JSON Schema the answer must follow, e.g.\n"
                       '{"type":"object","properties":{"city":{"type":"string"}}}\n'
                       "The answer then comes back as JSON text.",
    "preview": "The exact JSON body sent to POST /v3/workspaces/{workspace}/peers/{peer}/chat.\n"
               "'stream' is left at its default (false): the viewer waits for the full answer.",
}

RATINGS = {
    "correct": "Correct: the conclusion is true (key 1)",
    "wrong": "Wrong: the conclusion is false or invented (key 2)",
    "unsure": "Unsure: can't tell / partly right (key 3). Not counted in accuracy.",
    None: "Clear the rating (key 0)",
}

SNAPSHOT = ("Saves the current list of conclusions on this PC. After Honcho dreams (or processes more "
            "messages), click 'What changed?' to see what was added or removed.")
SUPPORT = ("Searches the raw messages for text similar to this conclusion, so you can check "
           "whether it was really said.")
