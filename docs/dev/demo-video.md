# Demo Video Design

## Goal

A short (2–3 min) video that shows what CodeFission *feels* like, not just what it does.
The hook is the tree — watching it grow and branch is the moment people get it.

---

## Structure

### 0:00–0:15 — Hook (no talking, just visuals)
Open directly on a finished tree with 6–8 nodes, branches glowing, running in parallel.
No intro, no logo yet. Let the visual speak first.
Cut to black. Then: **"This is CodeFission."**

### 0:15–0:40 — The problem (voiceover)
> "When you're exploring a hard problem with AI, you make a choice — and you're stuck with it.
> You can't go back. You can't try both. CodeFission changes that."

Show: a single chat thread that's gotten long and messy. Then wipe to the tree.

### 0:40–2:00 — The demo (screen recording, no cuts, real time)
Pick one of the example scenarios below. Keep it unscripted and natural — mistakes are fine,
they show it's real.

**Pacing tip:** don't wait for responses. Use a pre-warmed tree so responses are instant,
or speed up 3–4x during AI thinking time.

### 2:00–2:20 — The branch moment (the money shot)
This is the key scene. Show:
1. A completed response
2. Click branch
3. Two nodes side by side, both running, different approaches
4. Zoom out to show the full tree

Slow this part down. Add a subtle sound effect on the branch click.

### 2:20–2:40 — Outro
Show the tree one more time, zoomed out.
Fade to: `uv tool install codefission` typed in a terminal.
Then the URL appears. Done.

---

## Example Scenarios (pick one)

### Option A — Web app branching (best for general audience)
1. Start: "build me a todo app in vanilla JS"
2. It builds. Works.
3. Branch: "now make it use React instead"
4. Branch the original again: "add drag-and-drop sorting"
5. Show all 3 running simultaneously — different ports, different filesystems

**Why it works:** everyone understands a todo app. The branching is immediately obvious.

### Option B — Data science (best for technical audience)
1. Start: "analyze this CSV and find the most interesting pattern" (use a real dataset)
2. Branch: "try a different visualization"
3. Branch: "fit a model and show predictions"
4. Show plots embedded in the responses

**Why it works:** the _artifacts/ embedding of images is visually impressive.

### Option C — Debugging (most relatable)
1. Start with a broken piece of code
2. Ask Claude to fix it — it tries one approach
3. Branch and ask Codex the same question — different fix
4. Both run tests — one passes, one doesn't
5. Merge the winner

**Why it works:** shows multi-agent, shows real workflow, shows why isolation matters.

**Recommended: Option A** — fastest to set up, clearest payoff visually.

---

## Recording Setup

- **Resolution:** 1920×1080 minimum, 2560×1440 preferred (retina screen recording)
- **Browser:** Chrome, maximized, no bookmarks bar, no extensions visible
- **Font size:** bump terminal and browser font up 2–3px from normal
- **Theme:** dark mode everything — OS, terminal, CodeFission
- **Mouse:** use a cursor highlighter so clicks are visible (Mouseposé on macOS)
- **Clean desktop:** hide dock, close all other apps, plain dark wallpaper

---

## Editing

### Recommended app: ScreenFlow (macOS, $130)
Best balance of screen recording + editing for solo devs. Does callouts, zoom,
speed changes, annotations. Export presets for Twitter/YouTube.

### Free alternative: DaVinci Resolve
Full pro editor, free tier is plenty for this. Steeper learning curve but
much more control over color grading and motion.

### ffmpeg pipeline (no editing app needed)
If you just want to cut, speed up, and overlay — I can write you ffmpeg scripts for:
- Trim clips
- Speed up 4x during AI wait times
- Fade in/out
- Add text overlay for the install command
- Stitch clips together
- Export to H.264 for Twitter/YouTube

Just record raw .mov files and let me handle the rest.

---

## Visual Style

- **Color palette:** dark background (#0d1117), electric blue (#3b82f6) for nodes,
  soft purple (#a855f7) for branches, white text
- **Music:** lo-fi or ambient electronic — nothing with lyrics. Keep it low.
  Suggestion: search "dark ambient coding music no copyright" on YouTube
- **Captions:** add minimal captions for key moments ("branch", "isolated worktree",
  "running in parallel") — white text, bottom center, no background box
- **Speed:** normal for typing and clicking, 3–4x for AI responses, slow-mo (0.5x)
  for the branch moment
- **No zoom calls, no face cam** — pure product. Let the UI do the talking.

---

## Publishing

| Platform | Format | Length | Notes |
|----------|--------|--------|-------|
| Twitter/X | MP4, square or 16:9 | ≤ 2:20 | No sound autoplay — captions critical |
| YouTube | MP4 16:9 | Full length | Add chapters in description |
| GitHub README | GIF or MP4 (≤ 10MB) | 30–45 sec highlight | Embed directly in README |

For the README embed, extract just the branch moment + tree zoom out.
ffmpeg can do this — ask me when you have the raw recording.
