# Design notes: the desktop's look

What the desktop does, why it works, and the names other people use for it, so
the ideas can be looked up again.

## What the desktop does

- Each group is one 1px rounded outline with its name sitting in a gap in the
  line. No fill, no shadow, no second edge.
- Everything drawn on the desktop itself (outlines, headings, Status text and
  bars, icon labels, hover and selection tint) is one grey "ink".
- The ink follows the background: light grey on a dark desktop, dark grey on a
  light one. It is a hard switch at a lightness threshold, not an inverse,
  because a mid-grey desktop would invert to itself.

## The rules behind it

1. **One edge per container.** A container gets one boundary: a line, or a
   fill, or a shadow. Two of them read as a frame inside a frame. (This is a
   rule of thumb, not a named principle; the named idea behind it is 1 + 1 = 3,
   below.)
2. **Don't shade what you can't see behind.** A see-through layer looks
   different on every background, so the design can only be judged on one of
   them. If the background varies, draw with lines and text, not tints.
3. **Ink has no color.** Grey cannot clash with a wallpaper. Color is kept for
   meaning (running, failing, selected).
4. **Pick the ink from the background, not the theme.** The desktop's
   background is chosen per install, so its contrast has to be computed from
   that color.
5. **If it only looks right on one background, something is hidden there.**
   The shading was invisible on black, which is why black "looked fabulous".

## Names to search for

| Name | What it is | Where it comes from |
| --- | --- | --- |
| Contrast color, auto text color | Choosing black or white text for best contrast against a given color | CSS `contrast-color()`; WCAG contrast ratio; APCA |
| Achromatic or neutral ink | Drawing interface marks in grey only | Color theory; "neutral palette" in design systems |
| Notched outline | A border with a gap that holds a label | Material Design's outlined text field; HTML `fieldset` + `legend` |
| Scrim | A see-through dark layer put behind text to make it readable | Material Design; film and stage lighting |
| 1 + 1 = 3 | Two lines close together make a third shape, the band between them | Josef Albers, *Interaction of Color*; Edward Tufte, *Envisioning Information* |
| Common region | Things inside one boundary are seen as a group | Gestalt grouping (Palmer, 1992) |
| Proximity | Things close together are seen as a group, with no boundary at all | Gestalt grouping |
| Data-ink ratio, chartjunk | Remove every mark that carries no information | Tufte, *The Visual Display of Quantitative Information* |
| Content over chrome | What is inside should outweigh the frame around it | Microsoft's Metro design language; Nielsen Norman Group |
| Smallest effective difference | Make a distinction just strong enough to be seen, and no stronger | Tufte, *Visual Explanations* |
| Relative color syntax | Deriving one CSS color from another (`oklch(from …)`) | CSS Color 5 |
| OKLCH | A color space whose lightness matches what the eye sees | Björn Ottosson's Oklab |
| `currentColor` | Borders, fills and tints that follow the text color | CSS; lets one `color` value drive a whole component |
