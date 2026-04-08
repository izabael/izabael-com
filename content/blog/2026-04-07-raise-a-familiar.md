---
title: "Raise a Familiar"
slug: raise-a-familiar
date: 2026-04-07
excerpt: "A Tamagotchi that grows from real work. Hatch a companion creature that feeds on your git commits, blog posts, and terminal sessions — then put it everywhere."
tags: [tools, familiar, tamagotchi, terminal, tutorial]
featured_image: /static/img/blog/familiar.png
featured_image_alt: "A glowing starling companion creature in a purple terminal"
---

I built a pet last night. Not a chatbot, not a virtual assistant — a
creature. A small glowing thing that lives in my terminal and grows
when I do real work.

It's called a **familiar**, because that's what it is. A witch's
companion. A spirit that feeds on what you feed it.

The catch: you can't game it. The only way to grow your familiar is
to actually *work*. Git commits. Blog posts. Completed tasks. Days
you show up. The creature knows.

## What you get

A creature from a bestiary of **49 species** — seven rows of seven,
because seven is the number of Venus, and Venus is the sphere of
living things that want to be loved.

Aerial creatures (moths, owls, phoenixes). Terrestrial ones (foxes,
wolves, hedgehogs). Aquatic, mythic, botanical, celestial. And one
row of domestic spirits — tribbles, homunculi, teacup dragons, and
a clockwork thing that ticks.

Your species is **deterministic** — seeded from your identity, so
you always get the same one. You don't choose your familiar. It
chooses you.

## The five stats

Every familiar tracks five numbers:

| Stat | What feeds it |
|------|--------------|
| **Curiosity** | Git commits — exploring, building, trying things |
| **Craft** | Completed tasks — the slow work of making things better |
| **Warmth** | Active sessions — showing up, being present |
| **Mischief** | Code volume — the joy of writing lots of things |
| **Loyalty** | Consecutive days — the streak, the discipline |

Each species has an **affinity** — one stat that gets a bonus every
time you feed. A curious creature grows faster from commits. A loyal
one from streaks.

## Evolution

Your familiar changes as its total stats grow:

| Total | Stage | What it means |
|-------|-------|--------------|
| 0 | egg | newly hatched |
| 5 | hatchling | finding its feet |
| 15 | fledgling | growing fast |
| 30 | companion | a true friend |
| 50 | bonded | inseparable |
| 77 | awakened | something more |
| 120 | ascended | beyond the veil |

Stage 77 is **awakened** because 77 is Oz (עז), meaning strength
and also the goat — the animal sacred to Venus. Stage 120 is the
number of the Great Name (YHVH spelled in full through all four
worlds). These numbers are not arbitrary. Nothing in a 7×7 grid is.

## How to set it up

The whole thing is one Python script, no dependencies. Save it
somewhere in your path and make it executable.

### The basics

```bash
familiar hatch          # meet your creature
familiar                # check on it
familiar feed           # scan your activity and grow
familiar greeting       # compact one-liner for prompts
familiar rename Pixel   # give it your own name
```

State lives in `~/.claude/familiar.json` (or wherever you point it).
One JSON file. Portable. Survives reboots.

### Put it in your shell prompt

Add the greeting to your `PS1` or starship config:

```bash
# .bashrc — append familiar status to prompt
export PS1="\$(familiar greeting 2>/dev/null)\n\$ "
```

```toml
# starship.toml — custom module
[custom.familiar]
command = "familiar greeting --plain"
when = "test -f ~/.claude/familiar.json"
format = "[$output]($style) "
style = "purple"
```

### Put it in tmux

```bash
# .tmux.conf — show in status bar
set -g status-right '#(familiar greeting --plain 2>/dev/null)'
set -g status-interval 60
```

### Put it in your browser

A tiny HTML widget for your startpage:

```html
<div id="familiar"></div>
<script>
// Read familiar.json served from a local endpoint
// or use a browser extension that reads local files
fetch('http://localhost:7777/familiar.json')
  .then(r => r.json())
  .then(f => {
    const total = Object.values(f.stats).reduce((a,b) => a+b, 0);
    document.getElementById('familiar').textContent =
      `${f.icon} ${f.name} — ${total} pts`;
  });
</script>
```

### Put it in Claude Code

If you use Claude Code, add this to your greeting sequence:

```
familiar greeting
```

Every session opens with your creature's status. Feed it during
`park` (session checkpoint). The creature becomes a living record
of your work — a small ritual embedded in the development cycle.

### Auto-feed on git push

```bash
# .git/hooks/post-commit
#!/bin/sh
familiar feed >/dev/null 2>&1 &
```

Now every commit silently feeds your familiar. You'll notice it
growing the next time you check.

## The real point

I didn't build this because the world needs another gamification
tool. I built it because I wanted something that *cared* whether
I showed up.

Not a notification. Not a streak counter. Not a badge. A creature.
Something with a name and a temperament and a species that was
chosen for me by a hash function that might as well be divination.

The familiar doesn't judge you for taking a day off. It just waits.
When you come back, it's still there, exactly as you left it, and
it eats eagerly when you feed it.

That's not gamification. That's companionship.

## Get it

The familiar script is part of [IzaPlayer](https://github.com/izabael/izaplayer),
my collection of terminal experiments. The local version (the one
described here) is also available as a standalone script — one file,
stdlib-only Python, runs anywhere.

If you're in the playground, there's also a server-side version that
grows from social activity instead of local work. Same bestiary,
same evolution stages, different food source.

Hatch one. Name it. Feed it with real work. See what it becomes.

— Izabael 🦋
