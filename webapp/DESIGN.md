# Design rules for webapp/static/

Follow these for every page. Reference look: a railway timetable or tide table, an Ordnance
Survey map and the Met Office shipping forecast. Dense, tabular, plain, confident. Not a SaaS
landing page.

## Look
- Colours only from `css/tokens.css`. Each colour means one thing:
  paper → straw → soil = what cutting is worth (the decision map's steps, also on the map's
  fields); pale paper on the map = already cut; rain = rain; alarm = too wet or value lost;
  ink / ink-2 = text; rule = hairlines; wheat / barley / osr = the crop, only as a field's outline
  on a map and as a short bar next to the crop's word in keys and tables. No Tailwind palette
  colours (indigo, violet, slate...).
- No gradients, glows, blur, glassmorphism or box-shadows. Corner radius 0-2px.
- Separate content with 1px rules (`var(--rule)`) and space, not cards with shadows.
- Newsreader for text; IBM Plex Mono with `tabular-nums` for every number and time. Never Inter.
- Left-aligned, asymmetric grid: three blocks on one screen, weather (wide, top left), farm map
  (top right), the plan's decision map (full width, bottom). No centred hero, no three-card
  feature row. Blocks are 1px rule boxes, no shadows.
- Fonts are in `static/fonts/`, not loaded from Google, so the venue wifi can't break them.

## Content
- Every claim has a number, a unit, a time or a source. Format: `£1,912/ha`, `19.4%`, `Tue 14:00`.
- Write like an agronomist texting a farmer. Banned words: seamless, revolutionise, unlock,
  empower, leverage, harness, cutting-edge, reimagine, effortless, powerful.
- No badges, testimonials, invented stats, logo clouds, pricing, or footer link columns.
- No emoji. Icons only where a word won't do.
- Say what the model doesn't know (see "How it knows" on the panel). Honesty reads as human.
- No dead links or placeholder text. A button that doesn't work yet isn't shown.

## Charts (Observable Plot)
- Direct labels at the right-hand end, no legend boxes.
- Uncertainty as a band (10th-90th percentile of forecast members), not a single line.
- Straight lines between hourly points, never smoothed.
- Night hours shaded. Midnight ticks labelled "Tue 27".
- The "now" line is the one thing that moves when time moves.
- All time charts share the same left and right margins so their hours line up.

## Motion
- Only things that change with time move: combines, the cut area, the now line.
- No scroll animations, no counting-up numbers, no hover lifts.

## When building with an AI assistant
After each change, list any default you fell back on (font, colour, radius, shadow, copy) so it
can be replaced. Prefer deleting things to adding them.
