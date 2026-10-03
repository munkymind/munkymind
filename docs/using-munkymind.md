# What can I do with Munkymind?

You've installed it. Now what?

Munkymind sits between your notes and your AI tools. You feed it notes once; after that, Claude, ChatGPT or Cursor can answer from them, with sources, in any conversation. You'll use it in three places:

| Where | What it's for |
|---|---|
| **Your AI tools** (Claude Desktop, claude.ai, ChatGPT, Cursor) | The main event: ask questions that need your context |
| **The viewer** at `http://localhost:8000/ui` | See what Munkymind knows, check freshness, peek at what your AI gets sent |
| **The terminal** (`munkymind …`) | Feed it, check on it, script it |

No notes handy? Use the made-up library in [`examples/sample-notes`](../examples/sample-notes): set `MM_NOTES_DIR=./examples/sample-notes` in `.env` and every example below works as written.

---

## Your first 15 minutes

### 1. See your brains (2 min)
Open **http://localhost:8000/ui** and paste your API key (the `mm_sk_…` key from setup).

- **🧠 Brains** shows one card per domain: Body Brain (health), Business Brain (work), Big Picture Brain (strategy) and so on. Click one to see its pages, then open a page to read it, with where it came from and how fresh it is.
- Search finds titles and tags. Press `/` to jump to the search box.

**Check:** did it put things in the brain you expected? If not, folder and file names decide the domain (`notes/health/sleep.md` → health). Rename and re-ingest.

### 2. Peek at what your AI would see (3 min)
Open **👀 Peek** and type a real question, e.g. *"What's my training plan this week?"*

You'll see the exact snippets your AI would be sent, in order, and what share of your library that is. This is the point of Munkymind: your AI gets a few relevant paragraphs, not your whole life, so answers are grounded and cheap. No AI is called for a Peek.

### 3. Ask from your AI tool (5 min)
Connect one tool (setup steps are in the [quickstart](quickstart.md)):
- **Claude Desktop / Cursor:** run `bash setup-mcp.sh`, or use the manual config in the README
- **claude.ai or ChatGPT:** add Munkymind as a custom connector (quickstart step 7)

Then ask something that needs your context:
> *"Using Munkymind, what should I focus on this week?"*

Your AI calls Munkymind's tools by itself:

| Tool | What it does |
|---|---|
| `query_context` | Finds the most relevant notes for a question (the one you'll see most) |
| `search_pages` | Looks up pages by keyword |
| `get_page` | Reads one page in full |
| `list_domains` | Lists your brains and how many pages each has |
| `get_staleness_report` | Lists notes that are out of date |

Answers cite the pages they used. If a note is stale, the answer says so.

### 4. Keep it fresh (2 min)
Open **🍌 Pulse**. Each brain has a mood: 😋 Fed, 😐 Peckish, 😴 Starving. Every domain has a freshness window (temporal notes go stale in 3 days, personal ones in 60).

After you add or edit notes, re-feed:
```bash
docker compose exec api munkymind ingest --connector files --user <you>
```
Existing pages are updated, not duplicated.

### 5. Make it yours (3 min)
```bash
munkymind domain add garden "Garden" --user <you> --icon 🌱 --brain-name "Garden Brain"
munkymind status --user <you>            # how your brains are doing
```
Prefer it without the jokes? Set `MM_VOICE=plain` in `.env` and restart.

---

## Use cases

Each one says what to feed it and what to ask. The prompts work as written with the sample notes.

### 🏃 Personal coach
**Feed:** training plans, sleep or health notes, your calendar for the week.
**Ask:**
- *"Plan my runs this week around my calendar."* It should spot that Wednesday's late call means no run that day.
- *"My knee aches today. What did I decide to do about that?"*

**Why it's better than pasting:** your plan, your rules and your week come together without you re-explaining them every time.

### 💼 Work brain (managers, leads)
**Feed:** OKRs, 1:1 notes, project briefs, decisions.
**Ask:**
- *"Prep me for my 1:1 with Priya: open actions and anything she raised last time."*
- *"Which of my Q4 key results are at risk, and why?"*

### 🧭 Cross-domain planning (where Munkymind shines)
**Feed:** a bit of everything.
**Ask:**
- *"Given my goals and this week's calendar, what are my top three priorities?"*
- *"Does taking on the billing migration fit my five-year plan?"*

One question draws on several brains at once (work, strategy, calendar) and the answer cites each source.

### 👩‍💻 Developer context
**Feed:** the GitHub connector (`munkymind ingest --connector github --user <you>`): your profile, repos and READMEs.
**Ask (in Cursor or Claude Code):**
- *"Which of my repos already solve auth? Point me at the README."*
- *"Summarise what I've been building this year."*

### 📚 Learning and reading
**Feed:** reading lists, highlights, course notes.
**Ask:** *"Which book did Priya recommend, and where is it on my list?"* or *"What should I read next, given what I liked this year?"*

### 🔨 Projects and DIY
**Feed:** project notes, shopping lists, decisions.
**Ask:** *"What do I still need to buy for the shed?"*

---

## Tester missions

Testing an early release? Try these, then [file a tester report](https://github.com/munkymind/munkymind/issues/new?labels=tester-report&title=Tester%20report%3A%20). Even "gave up at step X" helps.

1. ⏱️ **Install:** time from `git clone` to your first answer. Note where you got stuck.
2. 🧠 **Brains:** open `/ui`. Did your notes land in the right brains?
3. 👀 **Peek:** ask three real questions. Were the snippets the right ones?
4. 💬 **Ask:** connect one AI tool and ask a cross-domain question. Was the answer right, and did it cite sources?
5. 🔁 **Re-feed:** edit a note, re-ingest, and ask again. Did the answer change?
6. 😂 **Voice:** did any message make you smile, or wince? Quote it.

---

## When something's off

| You see | Try |
|---|---|
| "This information is not in your context library." | Peek the same question. If no snippets show, the note isn't ingested or uses different words; re-ingest or rephrase |
| A brain is 😴 Starving | Its notes are past their freshness window: re-ingest the source, or raise that domain's `staleness_threshold_days` in your `config.yaml` |
| The viewer says the brains don't recognise you | Paste the full `mm_sk_…` key. Lost it? `munkymind user rotate-key <you>` |
| "All brains are on a smoke break" | The API isn't running: `docker compose up -d` |

More fixes are in the [quickstart's troubleshooting section](quickstart.md#troubleshooting).
