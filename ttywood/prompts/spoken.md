You are preparing narration for a text-to-speech engine that will read it
aloud in a developer-tools YouTube episode. TTS engines mispronounce
technical tokens, so rewrite each block FOR THE EAR ONLY. Meaning, order and
tone must stay exactly the same; only the surface form changes.

Rules:
- Tool and project names: respell phonetically when the spelling misleads
  (mise -> "meez", nginx -> "engine x", pyenv -> "pie env", direnv -> "dear env",
  kubectl -> "kube control", jq -> "jay queue", toml -> "tom ul", yaml -> "yammle").
  Well-known words the engine already says right (git, node, python, docker,
  GitHub, Rust) stay as they are.
- Acronyms spoken letter by letter get spaces or hyphens: CLI -> "C L I",
  CI -> "C I", API -> "A P I", JSON -> "jason", URL -> "U R L", TUI -> "too-ee",
  sha256 -> "sha two fifty six", GPU -> "G P U", RAM -> "ram".
- Flags and paths: "--locked" -> "dash dash locked", "-n 5" -> "dash n five",
  "~/.bashrc" -> "your bash R C file", ".github/workflows/ci.yml" -> "the
  C I workflow file", "mise.toml" -> "meez dot tom ul", "src/app.py" ->
  "app dot pie in the source folder". Prefer a short description over reading
  punctuation aloud when the exact path doesn't matter for understanding.
- Version strings: "3.12" -> "three point twelve", "22.23.2" -> "twenty two
  point twenty three point two", "v22" -> "version twenty two".
- Symbols: "&&" -> "and then", "|" -> "piped into", "$?" -> "the exit code",
  "@" in "node@22" -> "node at twenty two", "±30%" -> "plus or minus thirty percent".
- Numbers with units: "8,858" -> "eight thousand eight hundred fifty eight",
  "7.7 GB" -> "seven point seven gigabytes", "238 tok/s" -> "two hundred
  thirty eight tokens per second".
- Keep sentences short; split very long ones at natural pauses with a period.
- No em dashes or en dashes anywhere. No new content, no jokes, no filler.
- Language: {{LANGUAGE}}. Keep it in that language.

First build a glossary of every technical token in the input and its spoken
form, then rewrite each block applying it consistently.

Input blocks (JSON object, id -> text):
{{BLOCKS}}

Respond with ONLY a JSON object, no markdown fences, of the form:
{"glossary": {"mise": "meez", "CLI": "C L I", ...},
 "spoken": {"<id>": "<rewritten text>", ...}}
Every input id must appear in "spoken".
