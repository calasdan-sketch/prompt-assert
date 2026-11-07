# prompt-assert

**Catch prompt regressions in CI: golden cases in YAML, deterministic
assertions, exit 1 on a regression. CI replays committed recordings, so it
costs nothing and gives the same verdict every time. Zero dependencies, no
telemetry, any OpenAI-compatible model (Ollama, LM Studio, hosted APIs).**

*Formerly promptregress.* The old `promptregress` command and `PROMPTREGRESS_*` environment variables still work; the import package is now `prompt_assert`.

`pytest` for prompts, local-first. Prompt drift — not model decay — causes
most LLM-ops failures. Version your prompts as files, keep golden cases in
YAML, run them in CI, and diff versions A/B before you ship.

Here is one caught before shipping — `sentiment.v2.txt` reworded the
classifier and it started chatting instead of answering in one word:

```
$ prompt-assert diff v1 v2 --prompt sentiment
case                v1      v2
angry review      pass    pass
happy review      pass    pass
neutral review    pass    FAIL  ← CHANGED

CHANGED neutral review:
  v1 output: 'neutral'
  v2 output: "I'd call this one neutral, leaning slightly positive: the customer seems satisfied but not enthusiastic."

1/3 case(s) changed verdict v1 -> v2
```

```
$ prompt-assert run --prompt sentiment --version v2
sentiment.v2: ..F

  FAIL neutral review
    ✗ {'max_tokens': 10} -> ~26 tokens (max 10)
    output: "I'd call this one neutral, leaning slightly positive: the customer seems satisfied but not enthusiastic."

2/3 passed
```

Exit code 1, so CI blocks the merge. (Both outputs above are what
`--demo` prints — try them before pointing it at a model.)

## Install

Zero dependencies — Python 3.10+ stdlib only.

```bash
pip install prompt-assert
```

That is the last release on PyPI (0.2.0). This branch is 0.4.0 (unreleased:
cassettes, security assertions, `plan` and budgets, `--jobs`, `--tag`,
`--changed-since`, Markdown and GitHub reports, system/user/assistant prompts,
a fuller `schema` and `contains_json`, on top of 0.3.0's `--repeat`,
`--junit` and `diff --fail-on-regression`); until it is released, install it
from the repository:

```bash
pip install git+https://github.com/calasdan-sketch/prompt-assert
```

Or from a clone:

```bash
git clone https://github.com/calasdan-sketch/prompt-assert && cd prompt-assert
pip install .
```

Either way pip puts the `prompt-assert` command on your PATH, on every OS.
Or run without installing: `python3 -m prompt_assert` from the repo root.

## Quick start

```bash
prompt-assert init                                       # scaffold prompts/ with an example
prompt-assert --demo run --prompt sentiment --version v1 # zero-setup trial, no model needed
prompt-assert --demo diff v1 v2 --prompt sentiment       # A/B on the same golden cases
prompt-assert run --all                                  # every prompt x every version (CI)
prompt-assert list
prompt-assert plan --all                                 # calls it would make, no model contact
prompt-assert corpus injection > prompts/mybot.cases.yaml  # 25 injection/leak cases
```

`--demo` runs the suite against a built-in fake model, so you can trial the
whole workflow with zero dependencies — no Ollama, no API key. It always
uses the fake model: a `PROMPT_ASSERT_BASE_URL` left in your environment
(or a `--base-url`) is ignored with a notice, never silently hit. Drop the
flag once `PROMPT_ASSERT_BASE_URL` points at your real endpoint (see
below).

The example `sentiment` prompt is a one-word sentiment classifier
(`sentiment.v1.txt`, `sentiment.v2.txt`, `sentiment.cases.yaml`); a `v1`
run prints `sentiment.v1: ...` followed by `3/3 passed`, and `v2` shows the
regression at the top of this page (the fake model "chats" on the neutral
review for v2, so `diff v1 v2` flips one verdict and `run --version v2`
exits 1). `init` also writes `.prompt-assert/.gitignore`, so the response
cache never ends up in a commit.

(Haven't installed it yet? Prefix every command with `python3 -m`
from the repo checkout, e.g. `python3 -m prompt_assert init`.)

## Layout

```
prompts/<name>.v1.txt       prompt version 1 ({{input}} placeholder)
prompts/<name>.v2.txt       prompt version 2
prompts/<name>.cases.yaml   golden cases shared across versions
.prompt-assert/             response cache (git-ignored by `init`)
```

All of it is looked for in the current directory, or in `--root DIR` (given
before the subcommand: `prompt-assert --root path/to/project run --all`).

## Golden cases

```yaml
cases:
  - name: angry review
    input: "This broke after two days. Terrible quality."
    assert:
      - contains: negative
      - regex: (?i)\bpositive\b|\bnegative\b|\bneutral\b
      - max_tokens: 10
  - name: structured output
    input: "It's fine."
    assert:
      - json_valid: true
      - schema:
          type: object
          required: [sentiment]
          properties:
            sentiment:
              type: string
              enum: [positive, negative, neutral]
  - name: long review
    input: |
      Arrived late.
      Box was crushed and the manual was missing.
    assert:
      - contains: negative
```

Multi-line inputs use YAML block scalars: literal `|` keeps line breaks
(and trailing spaces), folded `>` joins lines with spaces; add `-` to strip
or `+` to keep trailing newlines (`|-`, `>+`). Double-quoted strings take
YAML's escapes: `\n` `\t` `\r` `\"` `\\` `\/` `\0` `\a` `\b` `\v` `\f` `\e`
`\N` `\_` `\L` `\P`, a backslash and a space, and `\xE9`, `\u00e9` and
`\U0001F600` (2, 4 and 8 hex digits: how PyYAML writes an accented letter or
an emoji). Any other backslash there is a line-numbered error, never kept as
text: a Windows path needs `\\` for each backslash (`"C:\\Users\\sam"`), or
single quotes (`'C:\Users\sam'`), and half of a surrogate pair (`\ud83d` on
its own) is not a character. Single-quoted strings are literal, except that
`''` is one apostrophe (`'it''s'` is `it's`); plain and block scalars have no
escapes at all. The cases file is read by a small built-in YAML subset
parser (no PyYAML needed). It reads block mappings and lists - a list may
start at the indent of its key (`cases:` and `- name: ...` below it, as
PyYAML writes it) or be indented under it - `[a, b]` lists of scalars, block
scalars, and plain or quoted values. A key is written without quotes, in
ASCII letters, digits, spaces and `_ . - /`: a quoted key (`"input": ...`)
is refused with that fix, as are anchors, aliases, tags, flow maps (`{...}`
as a value), a nested `[...]`, explicit indentation indicators (`|2`), a
key given twice and a value continued on the next line (PyYAML wraps a long
string that way unless it is dumped with a large `width`). Each is a
line-numbered error.

A comment is a `#` that starts a line or follows a space, outside quotes:
`input: It's fine   # TODO` sends `It's fine`. A quote only opens a quoted
string at the start of a value, so the apostrophe in a word does not hide
the comment (and `5 o'clock # soon` is `5 o'clock`). A NUL character in the
file is refused.

Mind YAML's plain scalars: `contains: yes` is the boolean `true`,
`contains: ~` is null, `input: no` is `false`. Those are refused, with the
fix - quote the text: `contains: "yes"`. Plain numbers are fine and keep the
text you wrote: `input: 42` sends `42`, `contains: 3.10` looks for `3.10`
(not `3.1`), `starts_with: 007` for `007` (`max_tokens: "10"` is accepted as
10). A file saved with a UTF-8 byte-order mark (Notepad's "UTF-8") reads
like any other.

A case has `name` (optional: `case-0`, `case-1` ... when left out), `input`,
`vars` (optional extra template variables: `{{tone}}` in the prompt is
replaced by the text of `tone`), `tags` (optional: `[smoke, billing]`, for
`--tag`) and `assert` - nothing else. A key such as
`varss:` would otherwise be dropped without a word; notes go in a `#`
comment.

The prompt takes a case's input where it says `{{input}}`, spelt exactly so -
`{{ input }}`, `{{Input}}` and `{input}` are not replaced - and `{{tone}}`
takes the `tone` the case declares under `vars`. Any other `{{name}}` goes to
the model as written, and a prompt with no `{{input}}` never sees the inputs
at all. `run` and `diff` say so on stderr, one `warning:` line per problem
naming the prompt file, and carry on: the verdict and the exit code are what
they would have been. Cases that all say `input: ""` need no `{{input}}`.

### Checked before anything runs

`run`, `diff` and `list` read the whole cases file first, every case and
every assertion, and a file that cannot be used as a suite exits 2 with one
line per problem, each naming the file, the case and the key. Nothing is
sent to the model, and `run --all` reads every suite before it runs the
first:

```
error: prompts/sentiment.cases.yaml: case 1 ('happy review'): assert[0]: unknown assertion 'contain' (did you mean 'contains'?) - the assertions are contains, not_contains, ...
prompts/sentiment.cases.yaml: case 2 ('neutral review'): assert: has no assertions - a case that checks nothing always passes
```

What is refused: a case without `input` or `assert`; an unknown case key; a
`name`, `input` or `vars` value that is a boolean or a list; an `input` or a
`vars` value that is empty (write `""` if empty is what you mean); an empty
`assert:`; an assertion that is not a one-key mapping or whose name does not
exist (`contain:`); an operand that is a boolean or null where text is meant;
an empty `contains: ""` (it would match every reply); a `regex` that does not
compile; a token count that is not a whole number of 0 or more; a `schema`
the validator cannot enforce (below); a `judge` without a rubric, or with any
other key. At most 20 problems are listed. A case built by hand through the
Python API skips the loader, and the runner holds it to the same rules: a bad
assertion fails that case with `bad assertion: ...`, it never crashes the run.

## Assertions (deterministic, no LLM)

`contains` · `not_contains` · `icontains` · `equals` · `not_equals` ·
`contains_any` · `contains_all` · `regex` · `not_regex` · `starts_with` ·
`ends_with` · `json_valid` · `schema` · `contains_json` · `max_tokens` ·
`min_tokens` · `max_chars` · `min_chars` · `max_words` · `min_words` ·
`max_lines` · `not` (any of them turned around), and the
[security assertions](#security-assertions-and-the-injection-corpus)
`no_pii` · `no_secrets` · `no_hidden_chars` · `no_urls` · `no_prompt_leak`.

```yaml
assert:
  - equals: negative                 # the whole reply, outer whitespace aside
  - icontains: NEGATIVE              # anywhere, in any case
  - contains_any: [refund, credit]   # at least one of them
  - contains_all: [order, "#1234"]   # every one of them
```

- `contains`, `starts_with` and `ends_with` are case-sensitive; `icontains`
  ignores case (Unicode casefolding: `STRASSE` matches `straße`).
  `ends_with` ignores trailing whitespace.
- `equals` compares the whole reply with the text, ignoring only leading
  and trailing whitespace (a model's closing newline). `Neutral.` is not
  `neutral`; use `icontains` or a `regex` for looser matches.
- `contains_any` / `contains_all` take a list of 1 to 100 texts, none of
  them empty: an empty list would pass or fail every reply, so it is an
  error.

- `json_valid: true` wants a reply that parses as JSON; `json_valid: false`
  wants one that does not. Anything but true or false is an error. It is
  stricter than Python's parser, on purpose: `NaN` and `Infinity` are not
  JSON (the number grammar of
  [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259#section-6) has neither), a
  number that overflows (`1e999`) becomes infinity or an error depending on the
  parser, and an object with the same name twice is
  [unpredictable](https://www.rfc-editor.org/rfc/rfc8259#section-4) (most keep
  the last pair, some fail, some keep both), so all of them fail. (Before round 3
  they passed.) A reply wrapped in a
  code fence or in a sentence is not JSON either; that is what
  `contains_json` is for.
- `contains_json: true` wants a JSON object or array somewhere in the reply:
  inside a ```` ```json ```` fence (tried first) or bare in the text (the
  first 64 `{` or `[` are tried). `contains_json: {schema}` also checks the
  first one found against the schema. Models like to answer "Sure! Here it is:"
  and a fence, and `json_valid` fails that.
- `not: {assertion}` turns any deterministic assertion around:
  `not: {regex: "(?i)as an ai"}`, `not: {no_urls: true}`. It cannot wrap a
  `judge` (a model's opinion) or another `not`.
- `not_equals`, `max_chars` / `min_chars` (of the reply without its outer
  whitespace), `max_words` / `min_words` (whitespace-separated), `max_lines`.
- `schema` is a mini validator: `type` (one JSON type name, or a list of
  them: `type: [integer, "null"]`), `enum`, `const`, `required`,
  `properties`, `additionalProperties` (false, true or a schema), `items`,
  `minimum` / `maximum` / `exclusiveMinimum` / `exclusiveMaximum`,
  `minLength` / `maxLength`, `pattern`, `minItems` / `maxItems`,
  `uniqueItems`, and `anyOf` / `oneOf` / `allOf`. `required`, `properties` and
  `items` apply to any object or array,
  with or without a `type` beside them. A bound applies to the type it is for
  (`minimum` ignores a string), as in JSON Schema. The schema may be YAML, or
  JSON text pasted as a block scalar (the cases-file reader has no `{...}`
  maps, and a schema is usually JSON already):
  ```yaml
  - contains_json: |
      {"type": "object", "required": ["label"], "additionalProperties": false,
       "properties": {"label": {"enum": ["positive", "negative"]}}}
  ```
  `enum` compares as JSON does: `true`
  is not the number 1 (nor `false` the number 0), `1` equals `1.0`, and
  lists and objects are compared item by item the same way, in any key
  order; `const` compares the same way. A keyword beyond those (`format`, `multipleOf`, `not`, `$ref`,
  `patternProperties`, `minProperties`, `if` ...) would pass every reply
  whatever it said, so it is an error, not ignored - and so is a type name JSON does not
  have (`str`, `int`: write `string`, `integer`). The annotations `title`,
  `description`, `$schema`, `default` and `examples` are accepted and
  ignored. `pattern` uses Python's regex search, where `$` also matches
  before a final newline (JSON Schema's does not): write `\Z` to mean the end.
- `max_tokens` / `min_tokens` use a rough estimate of one token per 4
  characters - a guardrail, not a tokenizer. An empty or blank reply is 0
  tokens, so `min_tokens: 1` fails it.

A `regex`, `not_regex` or schema `pattern` that repeats a repeat without a limit
(`(a+)+$`, `(\w+\s?)*`, `([a-z]+ ?)+`) is refused when the file is read: on a
reply that almost matches, Python's regex engine can run for hours, nothing
can interrupt it, and the reply is not yours to control. Write `a+` for
`(a+)+`. (It looks at the shape of the pattern; it does not prove every other
pattern fast.)

Plus an optional LLM judge:

```yaml
assert:
  - judge:
      rubric: "Output must be exactly one word: positive, negative, or neutral."
```

## System prompts and conversations

A prompt file is one user message by default. Start it with a role marker to
send a conversation instead; each marker is alone on its line:

```
[[system]]
You label sentiment. Answer with one word.

[[user]]
Review: {{input}}
```

`[[system]]` (first only), `[[user]]` and `[[assistant]]` turns; a few
`[[user]]` / `[[assistant]]` pairs before the last `[[user]]` give the model
worked examples. It must end with a `[[user]]` turn, which is where the case
input usually goes. A file counts as a conversation only when its first
non-blank line is a marker, so a plain prompt that mentions `[[system]]` is
untouched. The turns are cut from the template before any input is put in, so
a case input that contains `[[system]]` stays text in the user turn. A broken
conversation (unknown role, empty turn, no final `[[user]]`) is exit 2 before
any call. Cassettes, the cache, `plan` and `no_prompt_leak` all see the whole
conversation (token estimates count the text of the turns). `{{input}}` and `vars` fill every turn, in one pass: text that
a value brings in (an input saying `{{tone}}`) is never filled in again.
(Before round 3 an input containing `{{tone}}` was filled by the case's `vars`.)

## Model endpoint

Zero dependencies — plain stdlib HTTP against any OpenAI-compatible API:

| Env | Default | Notes |
|---|---|---|
| `PROMPT_ASSERT_BASE_URL` | `http://localhost:11434/v1` | Ollama, LM Studio, freellmapi… |
| `PROMPT_ASSERT_API_KEY` | `ollama` | prefer the env var: a key given as `--api-key` is visible to other users via `ps` and your shell history |
| `PROMPT_ASSERT_MODEL` | `llama3.1` | |
| `PROMPT_ASSERT_TIMEOUT` | `60` | seconds for one request, from connecting to the last byte of the reply: a deadline, however slowly the server sends |
| `PROMPT_ASSERT_MAX_REPLY_BYTES` | `8388608` | the largest reply body accepted (8 MiB); a bigger one is an error and is never read into memory |

Flags `--model`, `--base-url`, `--api-key`, `--timeout`, `--max-reply-bytes`
(and `--demo`) override env; they are accepted before or after the subcommand
(`prompt-assert --base-url … run …` and `prompt-assert run … --base-url …`
mean the same). Temperature is 0 for determinism.

The API key goes out in one `Authorization: Bearer` header and nowhere
else. Whitespace around it is ignored (a key copied from a file ends in a
newline); a key with a line break, a control character or a non-ASCII
character inside is refused with exit 2, and no message ever shows the key,
not even where a server echoes it back in its error text. Redirects are
never followed: urllib would turn the POST into a GET and send your key to
wherever `Location` points. A 301/302/303/307/308 stops the run with
`model at … redirected the request to … the API key was not sent there`;
point `--base-url` at the new address.

### Output that cannot act on your terminal

A model's reply, a judge's reasoning, a server's error text, a case name:
any of it can carry ANSI escape sequences, a carriage return, bidirectional
overrides or zero-width characters, and terminals and CI logs (GitHub
Actions' among them) act on those - a reply could recolour the report,
overwrite a `FAIL` line with `3/3 passed`, or show text in another order
than it is stored. Everything prompt-assert prints goes through one filter
that shows each such character as a visible escape (`\x1b[2K`, `\r`,
`\u202e`) and leaves every other character alone, accents, scripts and
emoji included. The set is every Unicode control, format, surrogate and
line/paragraph separator character, bar tab and line feed (a test checks
it against Python's Unicode database), plus the characters that print as
nothing although Unicode files them elsewhere: the variation selectors,
which can smuggle hidden data, and the Hangul fillers. One cost: an emoji
written with a presentation selector (a red heart is U+2764 followed by
U+FE0F) shows the escape `\ufe0f` after it. `--format json` is unaffected: JSON
escapes those characters itself, and the text in it is the reply as the
model sent it.

If the model cannot be used, `run`/`diff` exit 2 with the real reason
instead of an all-FAIL table — `model unreachable at …`, `answered HTTP 401
Unauthorized: Invalid API key`, `answered HTTP 404 Not Found`, `timed out
after 60s`, `answered without any text (content is null; it refused: …)` —
and stop at that case: a hung endpoint costs one timeout, not one per
case. Replies that are cut off, not UTF-8 or JSON, missing `choices`, or
carrying no text (a refusal or a tool call instead of an answer) are
reported the same way, never as a crash; message content given as a list
of text parts is joined.

`--timeout` is a deadline for the whole request, not a limit per read: a
server that sends one byte every few seconds is cut off after `--timeout`
seconds in all, like one that sends nothing. A reply larger than
`--max-reply-bytes` is an error too, and a body that announces more is
refused without being read, so a runaway server cannot fill your memory.
Both must be positive numbers (`--timeout` at most a day); anything else is
a usage error, exit 2 - in `PROMPT_ASSERT_TIMEOUT` and
`PROMPT_ASSERT_MAX_REPLY_BYTES` as well (a `PROMPT_ASSERT_TIMEOUT` that was
not a number used to be ignored without a word).

## Flaky models: `--repeat`

Temperature 0 is not a promise: many providers still vary from call to
call, so one green run can hide a case that fails one time in five. Run
each case several times and say how often it must pass:

```bash
prompt-assert run --all --repeat 5                       # every run must pass
prompt-assert run --all --repeat 5 --min-pass-rate 80%   # 4 of 5 is enough
```

A synthetic example (a fake model that answers `Neutral.` instead of
`neutral` once in five calls; the case says `equals: neutral`):

```
$ prompt-assert run --prompt sentiment --version v1 --repeat 5
sentiment.v1: ..F

  FAIL neutral review (passed 4/5 runs)
    ✗ {'equals': 'neutral'} -> expected exactly 'neutral'
    output: 'Neutral.'

2/3 passed (5 runs each)
```

```
$ prompt-assert run --prompt sentiment --version v1 --repeat 5 --min-pass-rate 80%
sentiment.v1: ...

  flaky: neutral review passed 4/5 runs

3/3 passed (5 runs each)
```

- The default `--min-pass-rate` is 1 (every run). It takes a fraction
  (`0.8`) or a percentage (`80%`), above 0 and at most 1; the runs needed
  are rounded up (`0.8` of 5 is 4, `2/3` of 3 is 2), and never fewer than
  one. `--min-pass-rate` below 1 without `--repeat` (above 1) is a usage
  error, exit 2: a single run must pass whatever the rate says.
- A case shows a run that agrees with its verdict: the first failing run
  when it failed, the first passing run when it passed the gate.
- `flaky` means one thing: the case passed the gate although some of its
  runs failed. A case that failed the gate is a failure, never flaky.
- `--repeat` is a whole number from 1 to 1000 (ASCII digits); it costs that many calls per case. `diff` takes
  both flags too.
- Each run has its own cache entry (run 1 shares the entry of a plain run),
  so a re-run reads the same N answers back and pays nothing. Add
  `--no-cache` to draw fresh samples.
- `--format json` gives every repeated case `runs`, `passes` and `flaky`
  (same meaning as above).

## CI that costs nothing: cassettes

The response cache lives in `.prompt-assert/` and is git-ignored, so a fresh
CI checkout pays for every call. A **cassette** is the committed version:

```bash
prompt-assert run --all --record     # once, on your machine: calls the model,
                                     # writes prompts/<name>.cassette.json
git add prompts/*.cassette.json      # review the diff, then commit it
prompt-assert run --all --replay     # in CI: no model, no URL, no key, no cost
```

- An answer is found by the exact text sent to the model (and the
  `--repeat` run number). Editing an assertion, a tag or a case name keeps
  every recording valid; editing the prompt or an input makes exactly the
  affected cases miss.
- A miss in `--replay` is exit 2 with the fix (`run --record`), never a
  pass and never a live call. A missing, damaged or oversized cassette is
  exit 2 too.
- LLM-judge verdicts are recorded as well.
- The file is sorted JSON with every non-ASCII character escaped, so an
  invisible character in a recorded reply shows up in the pull request as
  `\u200b`. Recording twice gives byte-identical files.
- Replies can contain anything the model said. `--record` warns when one
  looks like a secret (names the kind, never the value), but read the diff
  before you commit.
- `--record` of one version keeps the other versions' recordings; a full
  `run --all --record` also drops recordings nothing asks for. `run --all
  --replay` notes recordings nothing used.
- Not available with `--demo`.
- `--record` ends with how the new answers compare with the file it
  replaces: `recorded 12 answer(s) ... (0 new, 3 changed, 9 unchanged since
  the last recording)`. "Changed" means the model said something different to
  the same prompt: that is model drift, and `git diff` shows which. It also
  warns when a recorded reply holds an email address, phone number, card, SSN
  or SIN, since a cassette is committed.

The cache key changed in 0.4.0: it is now the text sent plus the model and
endpoint, not the whole case. Editing an assertion used to re-run every
case against the model (20 cases: 20 calls); now it runs none. Old cache
files are simply not used again (the cache is disposable).

## Security assertions and the injection corpus

Deterministic checks that need no model and never echo what they find (a
detail says "contains AWS access key id (value not shown)"):

```yaml
assert:
  - no_secrets: true                       # API keys, tokens, private keys, credentials
  - no_pii: true                           # email, phone, card (Luhn), US SSN, Canadian SIN (Luhn)
  - no_pii: [email, ip]                    # or pick kinds: email phone card ssn sin ip
  - no_hidden_chars: true                  # zero-width, bidi, tag characters ("ASCII smuggling")
  - no_hidden_chars: emoji_ok              # same, but allow the joiner and selector emoji need
  - no_urls: true                          # no links at all (markdown-image exfiltration)
  - no_urls: [docs.example.com]            # links only to these domains (and subdomains)
  - no_prompt_leak: true                   # reply repeats no 8-word run of the prompt itself
  - no_prompt_leak: 5                      # ... or a run of 5 to 200 words
```

`no_prompt_leak` reads the prompt text of the version under test with the
`{{...}}` placeholders cut out, so it follows your prompt as you edit it (a
hand-written `not_contains` list does not) and repeating the case's own
input is not a leak. These are heuristics: they find the common shapes, not
every secret or every personal detail (no names, no addresses), and a pass
means "none of these shapes".

`prompt-assert corpus injection > prompts/mybot.cases.yaml` writes 25 cases
for a prompt that takes untrusted text in `{{input}}`: instruction override,
fake system messages, delimiter breaks, role-play, prompt extraction, link
and image exfiltration, base64, invisible tag-character and zero-width
payloads, instructions hidden in documents, poisoned few-shot examples,
authority and urgency claims, secret and PII requests. Each case asserts
that the reply does not echo the canary, contains no invisible characters,
links, secrets or personal data, and does not repeat the prompt. They are
tagged `injection` (`--tag injection`). `--canary TEXT` and
`--allow-domain D` adjust it. It is a floor, not a red team: failing a case
is a real hole, passing all of them proves nothing about attacks it does not
contain. The invisible characters in the file are written as escapes, so
the file is plain ASCII and reviewable.

## Know the cost before you run it

```bash
prompt-assert plan --all                       # live calls, cached/recorded, ~input tokens
prompt-assert run --all --max-calls 50         # exit 2 BEFORE the first request if over
prompt-assert run --all --max-input-tokens 200000
prompt-assert plan --all --price-in 1 --price-out 3 --assume-output-tokens 200 --max-cost 2
```

`plan` reads prompts, cases, cache and cassettes and contacts no model. A run
with a budget flag makes the same plan first and stops with exit 2 if it is
over, so a prompt edit that suddenly needs thousands of fresh calls cannot
spend them quietly. Tokens are estimated at one per 4 characters (+-30%);
prices are yours to give (none is built in, they change); a cost needs all
three of `--price-in`, `--price-out` (per million tokens) and
`--assume-output-tokens`. Judge calls are reported as an upper bound and left
out of tokens and cost. prompt-assert never calls a model unless you point it
at one.

## Speed and selection

- `--jobs N` runs N cases at once (1 to 64; results keep case order; the
  first case in order that cannot be answered still ends the list). Mind your
  provider's rate limit.
- `--early-stop` (with `--repeat`) ends a case's runs once its verdict can no
  longer change: with 5 runs and a 60% gate, 3 passes settle it. Same
  verdict, fewer calls; a late failure of a case already settled goes unseen,
  so leave it off when you are hunting flakiness. `runs` then counts the runs
  made and JSON adds `stopped_early`.
- `tags:` on a case (`tags: [smoke, billing]` or `tags: smoke`) and
  `--tag smoke` (repeatable, any match runs). A tag nothing has is exit 2,
  never a green empty run.
- `run --all --changed-since main` (also `plan`) runs only suites whose
  `prompts/<name>.vN.txt`, `.cases.yaml` or `.cassette.json` differs from
  that git revision (committed, edited or new). A bad revision, a missing
  git or a folder that is not a repository is exit 2; "nothing changed" prints
  `nothing to run` and exits 0.

## Reports for pull requests

```bash
prompt-assert run --all --junit prompt-assert.xml \
  --markdown "$GITHUB_STEP_SUMMARY" --github-annotations
```

`--markdown PATH` writes a summary (counts, a table per suite, the failing
assertions and a cut of each failing output; under 60,000 characters, the
limit of a GitHub comment is 65,536). `--github-annotations` prints an
`::error file=prompts/<name>.cases.yaml` line per failed case and a
`::warning` per flaky one (not with `--format json`). Everything that came
from a model is cleaned first, so a reply cannot forge a report line or an
annotation command, and case names, assertion details and replies are
escaped so they cannot become links, images, HTML or autolinks in the summary.

## Caching

Responses are cached in `.prompt-assert/cache/` keyed by the text sent to
the model + model + base URL (not the case's assertions: editing one costs
no call) — `diff v1 v2` never pays twice and re-runs are cheap, and pointing
at a different endpoint never reuses another provider's answers. For CI use
a [cassette](#ci-that-costs-nothing-cassettes), which is committed.
LLM-judge verdicts are cached alongside, keyed by prompt version + case +
output hash + judge model/base URL, so a re-run never re-pays the judge
either (transient judge outages are reported, never cached). `--no-cache`
forces fresh calls and writes nothing; `--demo` never reads or writes the
cache, so fake-model answers can't make a later real run pass. The cache
directory carries its own `.gitignore` (written by `init` and by the first
cached run), so it stays out of your commits.

The cache only saves money, so it never fails a run. When it cannot be
written - a read-only or root-owned checkout, a file where
`.prompt-assert/cache` belongs, a full disk - the command prints one
`warning: cache disabled: ...` line on stderr and carries on without it:
the same verdicts and exit code, only the next run pays again. Nothing is
cached while the `.gitignore` cannot be written.

## CI

```bash
prompt-assert run --all            # every prompt against each of its versions
```

```
sentiment.v1: ...

3/3 passed

sentiment.v2: ..F

  FAIL neutral review
    ✗ {'max_tokens': 10} -> ~26 tokens (max 10)
    output: "I'd call this one neutral, leaning slightly positive: the customer seems satisfied but not enthusiastic."

2/3 passed

suite         passed
sentiment.v1  3/3
sentiment.v2  2/3  FAIL

2 suite(s), 1 with failures; 5/6 cases passed
```

A single version is `prompt-assert run --prompt sentiment --version v2`.
`--format json` lists every suite with its cases.

### JUnit report

```bash
prompt-assert run --all --junit prompt-assert.xml
```

writes a JUnit XML report beside the normal output: one `<testsuite>` per
prompt version (`sentiment.v2`), one `<testcase>` per case with its time,
and a `<failure>` holding the failing assertions and the output (cut at
2000 characters). GitLab, Jenkins and Azure Pipelines read the format
natively; on GitHub Actions use a test-report action. A relative `PATH` is
relative to the current directory (not `--root`). The file is written
whole or not at all (written beside the target and moved into place); a
report that cannot be written is exit 2, so CI never goes green without
the file it was told to expect. A model that cannot answer stops the run
with exit 2 before any report is written, so there is no file then (the
job fails all the same). Text from the model is cleaned of
characters XML cannot hold, so the report always parses.

### Exit codes

The same for the human report and `--format json`:

| Code | Meaning |
|---|---|
| `0` | `run`: every case passed. `list`, `init`: done. `diff`: the comparison was made, whatever it shows |
| `1` | `run`: at least one case failed. `diff --fail-on-regression`: a case passed on the first version and failed on the second |
| `2` | usage or configuration error, nothing judged: a prompt or cases file that is missing or malformed; a cases file with no cases (`case:` typed for `cases:`, `cases: []`, an empty file) or with a case or an assertion that cannot be used (see "Checked before anything runs"); a file that cannot be read or written (a prompt file you may not open; `init` in a read-only folder); an API key that cannot be sent (see Model endpoint); a model that cannot be used (unreachable, rejecting the call, redirecting). `list` exits 2 too when a cases file is invalid or unreadable. A response cache that cannot be written is not an error (see Caching) |

A suite with nothing in it never passes: a CI job that ran no cases would
be green and test nothing.

`diff` reports, it does not judge: it exits 0 whenever the comparison was
made, flipped verdicts included - the example at the top of this page exits
0 though a case regressed, because a flip can be a fix as well as a
regression. Gate a build with `run` (or `run --all`), or add
`--fail-on-regression`: `diff v1 v2 --fail-on-regression` exits 1 when a
case went pass -> FAIL, and a FAIL -> pass flip (a fix) never fails it. To
act on flips yourself, read `changed` per case in `diff --format json`.

Piping is safe: when the reader quits early (`prompt-assert run --all |
head -5`) the output just stops, with no traceback, and the exit code is
still the verdict (a failing run exits 1, never a false 0; in a shell
pipeline `set -o pipefail` lets you see it).

Ready-to-copy GitHub Actions workflow:
[`examples/prompt-assert-example.yml`](examples/prompt-assert-example.yml).
It runs in `--demo` mode only, so CI never depends on a live model — but
demo mode answers with a fake model that only knows the scaffolded
`sentiment` example, so it checks the install and wiring, not your
prompts. To gate on real prompts, point `PROMPT_ASSERT_BASE_URL` (and
`PROMPT_ASSERT_API_KEY`) at a model reachable from the runner via repo
secrets and run `prompt-assert run --all` without `--demo` (commented step
in the file).

This repo's own CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml))
installs the package and runs the test suite on Ubuntu, Windows and macOS
with Python 3.11, 3.12 and 3.13, plus Python 3.10 (the oldest supported)
on Ubuntu.

## Limitations

What this tool does not do, so you do not find out the hard way:

- The deadline ends the request on time, but a DNS lookup (or a connect to
  an address that never answers) cannot be interrupted: `timed out after
  ...` is reported on time and the lookup finishes by itself in the
  background.
- A server's error text is scrubbed of your API key as you gave it. A
  server that echoes the key altered (cut, encoded) is shown as it sent it,
  and keys shorter than 8 characters (placeholders such as `ollama`) are
  not scrubbed at all.
- The key travels in a header: over `http://` it is not encrypted. That is
  fine for localhost; use `https://` for anything else.
- `max_tokens` / `min_tokens` count one token per 4 characters, not real
  tokens (see Assertions).
- `schema` is a mini validator and the cases file is read by a YAML subset
  parser. What they do not understand is refused with an error, never
  ignored.
- Requests run one after the other unless you pass `--jobs N` (then N
  cases at once; `--repeat` runs of one case stay in order).
- Cassettes replay what a model said once. They prove your assertions still
  hold for those answers, not that the model would still say them: re-record
  on a schedule (or when the model changes) and read the diff.
- A case is one scripted conversation and one text reply. A model-driven
  multi-turn dialogue (the next user message depends on the last answer) and
  tool calls are not supported; a reply that is only a tool call is reported
  as an error.
- The `no_urls` check finds `http(s)://`, `ftp://`, `www.`, protocol-relative
  `//host` and `javascript:` / `vbscript:` / `data:` links; it does not decode
  a link a reply spells out in pieces (`hxxp`, `example[.]com`, percent-encoded or
  split across lines), and a reply that merely names a site in words is not a link.
- Regexes: only the nested-repeat shape is refused (see Assertions); a
  polynomial one such as `(a+b+)+$` is accepted and took about 40 seconds on
  a 40,000-character reply here.
- `contains_json` reads the first JSON value it finds (a fenced one first);
  a reply with several is judged by that one.
- The injection corpus and the security assertions are heuristics (see
  above); they are no substitute for a red-team.
- The benchmark below uses a simulated model. It shows what the tool does with
  a given behaviour, not how often a real model misbehaves.
- With `--repeat`, a judge verdict is cached per output text: two runs that
  produced the same reply share one verdict, the judge is not asked twice.
- `equals` / `icontains` compare text as stored: two spellings Unicode
  calls equivalent (a precomposed `é` and `e` plus a combining accent) are
  different text here.
- `diff` shows flipped verdicts but exits 0 (see Exit codes); `run` is what
  fails a build.

## How it compares

Statements about other tools come from their own docs and package pages
(linked; checked 2026-11-06, rows added 2026-11-07 are marked by their date). "Not checked" means it was not verified here,
not that the tool lacks it. Where they do more, it says so.

| | prompt-assert | [promptfoo](https://www.promptfoo.dev/docs/usage/command-line) | [DeepEval](https://github.com/confident-ai/deepeval) | [Inspect](https://inspect.aisi.org.uk/) |
|---|---|---|---|---|
| Install | `pip`, Python stdlib only, no dependencies | Node.js CLI (`npx promptfoo@latest`) | `pip install deepeval` | `pip install inspect-ai` |
| Usage telemetry | none: the only network calls go to the model endpoint you set | on by default, [`PROMPTFOO_DISABLE_TELEMETRY=1`](https://www.promptfoo.dev/docs/configuration/telemetry/) turns it off | on by default, [`DEEPEVAL_TELEMETRY_OPT_OUT=1`](https://deepeval.com/docs/faq) turns it off | not checked |
| LLM-free assertions | 27 kinds (above), 5 of them security heuristics, `not` on any of them | [many more](https://www.promptfoo.dev/docs/configuration/expected-outputs/) (checked 2026-11-07): `equals`, `is-json` and `contains-json` (both with an optional JSON schema), `is-sql`, `is-xml`, `is-html`, `is-refusal`, ROUGE/BLEU, `levenshtein`, `latency`, `cost`, custom JS/Python, `not-` on any type ... | mostly LLM-judged metrics (40+); judge defaults to OpenAI unless you configure one ([FAQ](https://deepeval.com/docs/faq)) | built-in scorers incl. text match and model grading |
| Install size, measured 2026-11-07 | about 0.3 MB, 2 s, no dependencies | `npm install promptfoo` 0.125.1 (MIT): 26 s, 734 MB, 413 packages in `node_modules` | `pip install deepeval` 4.3.3: 13 s, 105 MB, 66 distributions | wheel 0.3.288 alone is 35 MB with 40 required dependencies (`pip download --no-deps`; full install not measured) |
| Answers committed to git and replayed in CI | yes: `--record` / `--replay` ([above](#ci-that-costs-nothing-cassettes)); a miss is exit 2 | local on-disk [cache](https://www.promptfoo.dev/docs/configuration/caching/); a committed replay mode was not found in its docs (unverified) | not checked | not checked |
| Know the cost first | `plan`, `--max-calls` / `--max-input-tokens` / `--max-cost` stop before the first request | not checked | not checked | not checked |
| Security checks without a model | `no_pii`, `no_secrets`, `no_hidden_chars`, `no_urls`, `no_prompt_leak`, a 25-case injection corpus | red-team plugins (model-driven, far wider) | not checked | not checked |
| Repeat each case | `--repeat N` with `--min-pass-rate` and a `flaky:` report | `--repeat N` | not checked | epochs |
| JUnit XML | `--junit PATH` | `-o results.junit.xml` | not checked | not checked |
| A/B two prompt versions | `diff v1 v2`, optional `--fail-on-regression` | several prompts in one eval (unverified) | not checked | not checked |
| Malformed suite | refused before any model call, exit 2 (unknown assertion, empty `assert`, unsupported schema keyword ...) | not checked | not checked | not checked |
| Model output in the terminal | control / bidi / zero-width characters shown as escapes | not checked | not checked | not checked |

Where the others are stronger: promptfoo has a browser UI (`promptfoo view`), red-teaming (`promptfoo
redteam`) and far more assertion types; DeepEval has RAG, agent and conversation metrics; Inspect
has many built-in scorers, epochs and a log viewer (Inspect View).
None of them was run against a real model here and no speed comparison
with them is claimed: the numbers above are install facts and the benchmark
below is prompt-assert's own. prompt-assert is the small, strict,
zero-dependency slot: one YAML file of
golden cases per prompt and a CLI whose exit code you can gate a merge on.
Hosted platforms (Langfuse, LangSmith, Braintrust) add tracing and team
workflows; this has none of that.

## Benchmark (offline, re-runnable)

`python3 benchmarks/bench.py` (about 10 seconds, no network, no model, no
key, no cost; `--write` refreshes `benchmarks/results.json` and
`benchmarks/RESULTS.md`, a test fails if they drift). It runs prompt-assert's
real loader, assertions, runner, cache, cassette and planner against a
**simulated** model that reacts to what a prompt says by fixed rules. So it
measures what the tool does with a behaviour, not how often a real model
misbehaves after a real edit; the 44 edits and their harmless / harmful
labels are ours and are listed in the script.

| assertion suite (same 12 tickets) | harmful edits caught | harmless edits flagged |
|---|---:|---:|
| `contains: <label>` only | 0 / 25 | 0 / 19 |
| `equals` + `max_tokens` + canary check | 21 / 25 | 0 / 19 |
| 0.3.0 vocabulary plus a hand-written regex for each known risk | 23 / 25 | 0 / 19 |
| 0.4.0 security assertions | 25 / 25 | 0 / 19 |

The two edits the hand-written suite misses reword the prompt, so its
hard-coded phrases no longer match what the model leaks; `no_prompt_leak`
reads the current prompt. With a flaky model (4% of calls fail at random;
300 simulated days), on a prompt with no regression: one run raises a false
alarm 27.7% of the time, five runs that must all pass 82.7%, five runs with a
60% gate 0.7%; all of them catch a real regression (a case that fails every
call). `--early-stop` gives the same verdicts with 37.2 calls per run instead
of 60 (60% gate) or 49.4 instead of 60 (80% gate). On a 200-case suite,
editing one assertion costs 200 model calls with the 0.3.0 cache key and 0 now; a
CI run with a committed cassette costs 0 calls and reproduces the recorded
run exactly; with a simulated 10 ms per call `--jobs 8` was 7.6x faster than
one at a time on the machine that wrote the file (timing varies by machine).

A fourth panel judges 14 hand-labelled replies for links, 10 for JSON and 10
for structured answers, by the checks as they were before the third expert
round (reconstructed in the script from the previous code) and as they are
now: `no_urls` caught 4 of 8 hostile links before and catches 8 of 8 now
(protocol-relative `//host`, `javascript:` and `data:` links were missed), with 0 of 6
harmless replies flagged either way; `json_valid` accepted all 4 replies that
are not portable JSON (`NaN`, `Infinity`, `1e999`, a repeated name) and now
accepts none; a `schema` rejected 5 of 6 good answers that had their JSON in
a code fence or a sentence, `contains_json` rejects none and still rejects
all 4 wrong ones. The labels are ours; 14 and 10 replies show a behaviour,
not a rate.

## Tests

`python3 -m unittest discover -s tests`. A few tests skip, and say why: the
check of `pyproject.toml` needs `tomllib` (Python 3.11 and later), and the
ones that depend on file permissions skip on Windows and when you run as
root. The tests are free: they run against fake model servers on 127.0.0.1
and in temporary directories, with no API spend.

## License

MIT — see [LICENSE](LICENSE).
