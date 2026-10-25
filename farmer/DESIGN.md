# Design rules for farmer/static/

**The farmer site looks like the demo panel: follow `webapp/DESIGN.md` first.** Same colours and
fonts, read straight from the demo (`/brand/css/tokens.css`, `/brand/fonts/`), so one brand: paper
background, ink text, 1px hairlines instead of cards, Newsreader for words, IBM Plex Mono with
tabular figures for every number and time, square corners, no shadows, no gradients.

What changes for farmers:

## Status: a small square lamp and its word, never colour alone
| Status | Lamp | Meaning |
|---|---|---|
| ready | field green `--ready` | can be cut now |
| soon | straw `--soon` | a window opens within hours |
| wait | brick `--wait` | not today |
| done | hollow `--done` | all cut |

These four colours are only for field status (and the matching good / fair / poor hours on the
Weather page).

## Crop: the field's outline colour, and a short bar next to its word
Wheat, barley and OSR use the demo's crop colours (`--wheat`, `--barley`, `--osr`): the outline of
every field on the map, and a short bar (never a square, so it isn't read as a lamp) before the
crop's word in keys and tables. Weather maps keep their own colour scales because they are data, always with a legend.

## Few words, big answers
- Each page opens with one sentence that answers the question, e.g. "Cut today." then one line of
  why, built from the numbers.
- Everything else is tables with short column heads, figures in mono.
- No algorithm words (planner, ensemble, member, optimiser). Say ready, best time, plan, forecast.
- No icons, emoji, pills, rings, avatars or badges. A word or a lamp does the job.

## Controls
- Buttons: ink rectangles (main) or outlined (secondary), 44px tall, mono labels.
- Switches: a square ON / OFF box on the right of a hairline row.
- Buttons and switches on the control panel are pretend: they answer with a short message
  bottom-left and remember their state while browsing, but don't change the plan.

## Real and pretend
- Weather is real (Open-Meteo forecast, RainViewer radar), fetched live by the browser.
- Fields, combines and GPS come from the simulated season (`source.py`).
- Machine readings (fuel, tank, speed) are pretend.
