Write YouTube metadata for an episode of {{SHOW}}. Every episode is a
hands-on demo of one tool, ending with an honest review.

Episode subject:
{{CANDIDATE}}

Rehearsal notes (the reviewer's genuine findings):
{{NOTES}}

Scene timeline (final video timestamps, seconds):
{{TIMELINE}}

Write everything in **{{LANGUAGE}}** (section headings too; tool/command
names stay in Latin script). Style: a person talking, not marketing copy or an
AI assistant. NEVER use em dashes or en dashes; use commas or periods. No AI
vocabulary or filler (delve, seamless, leverage, robust, game-changer,
unleash, elevate, supercharge, revolutionize, cutting-edge, "without further
ado", "let's dive in", "it's worth noting", "at the end of the day"). No hype.

Produce a markdown document with exactly these sections:

## Titles
5 options, <=70 chars, no clickbait lies. Curiosity is fine, false claims are
not. At least one plain-descriptive option. The repo "owner/name" is appended
to the title automatically at upload, so leave room and don't include it.

If the candidate has a `series` block, EVERY title option starts with the
series title and the zero-padded episode number exactly as
`<series title> E<nn>: `, e.g. `Zellij from zero E03: `. What follows is that
episode's own hook — the series title is not the episode's subject. Keep the
whole thing inside 70 characters, which means the hook after the prefix is
short. Say in the first line of the description which episode this is and what
it assumes, so someone landing on E03 knows where they are.

## Description
First 2 lines hook + what the video covers. Then the repo link (the star count
is added after it automatically). Then a
"Chapters" list derived from the timeline (format `MM:SS Scene name`, using the
scene start times converted from seconds). Then one honest line about the
verdict. End with: "Recorded in an isolated container. Not sponsored."

## Tags
15-20 comma-separated tags mixing the tool name, its category, and generic
terms (cli, terminal, open source, developer tools...).

## Thumbnail
Exactly two lines:
Big text: <at most 4 words, no punctuation>
Visual: <one sentence>

## Structure
One line: which episode structure this was (from the rehearsal notes), so the
next episodes can pick a different one.

Never copy the repo's own description, tagline or README lines into the title or description: write what the episode showed, in your own words. No emoji, no links except the repo URL, and none of the project's calls to action (join the Discord, star the repo, sign up).
