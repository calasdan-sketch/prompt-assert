"""Deterministic assertions for prompt outputs. No LLM needed.

Supported assertions (each is a one-key dict in the case's `assert` list):

  contains: str            output contains str
  not_contains: str        output does not contain str
  icontains: str           contains str, ignoring case (casefold)
  equals: str              the whole output is str (outer whitespace aside)
  contains_any: [a, b]     output contains at least one of the texts
  contains_all: [a, b]     output contains every one of the texts
  regex: pattern           re.search finds pattern
  not_regex: pattern        re.search finds nothing
  starts_with: str / ends_with: str
  json_valid: true         output parses as JSON (false: it does NOT)
  schema: {...}            parsed JSON validates (mini validator below)
  max_tokens: n / min_tokens: n   ~chars/4 token estimate (documented)
  not_equals: str          the whole output is anything but str
  max_chars / min_chars: n   characters of the output, outer whitespace aside
  max_words / min_words: n   whitespace-separated words
  max_lines: n             non-blank-trimmed lines (an empty reply has 0)
  contains_json: true | {schema}   a JSON object or array somewhere in the
                           reply (a ```json fence is read); optionally one
                           that validates against the schema
  not: {assertion}         the assertion must NOT hold (any but judge)

Security assertions (heuristics, see scan.py; they never echo what they find):

  no_pii: true | [email, phone, card, ssn, sin, ip]
  no_secrets: true         API keys, tokens, private keys, credentials
  no_hidden_chars: true | emoji_ok     zero-width / bidi / tag characters
  no_urls: true | [domain, ...]        no links, or only to these domains
  no_prompt_leak: true | N  the reply repeats no run of N (default 8) words
                           of the prompt text itself

Each returns (passed: bool, detail: str). A malformed assertion (unknown
kind, YAML null/bool where text is needed, a non-integer token count, an
invalid regex, a schema the validator cannot enforce) raises ValueError
with a message the runner shows as "bad assertion: ..." — never a TypeError
or re.error traceback.

A plain number given as text is the text it was written as: `contains: 3.10`
looks for "3.10", `starts_with: 007` for "007" (see miniyaml.scalar_text).

check() holds an assertion to all of those rules and returns it in working
form (text as text, counts as ints). The cases loader runs it on every
assertion of a file, so a bad one is a configuration error before any model
is called; evaluate() runs it again, so a case built by hand gets the same
verdict.
"""

from __future__ import annotations

import difflib
import json
import re
import xml.etree.ElementTree as ET

from . import miniyaml, scan


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON (JSON has no NaN or Infinity)")


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r} (parsers disagree on "
                             f"which value wins)")
        out[key] = value
    return out


def _strict_float(text: str):
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError(f"{text[:20]} is too large for a number")
    return value


_STRICT = dict(parse_constant=_no_constant, parse_float=_strict_float,
               object_pairs_hook=_no_duplicates)


def parse_json(text: str):
    """json.loads, but strict where Python is lenient: NaN, Infinity and
    -Infinity are refused (they are not JSON, and JSON.parse rejects them), so
    are numbers that overflow to infinity, and so is an object with the same
    key twice. ValueError (RecursionError for a document nested too deep)."""
    return json.loads(text, **_STRICT)


#: contains_json looks at this many starting points ({ or [) at most.
def xml_problem(text: str) -> str | None:
    """None when `text` is one well-formed XML document, else why not. A
    document type declaration (<!DOCTYPE, <!ENTITY) is refused outright:
    entity expansion is how a small reply becomes gigabytes."""
    body = text.strip()
    if not body:
        return "the reply is empty"
    if "<!DOCTYPE" in body.upper() or "<!ENTITY" in body.upper():
        return "a DOCTYPE or ENTITY declaration is not accepted"
    try:
        ET.fromstring(body)
    except (ET.ParseError, ValueError, RecursionError) as e:
        return str(e).splitlines()[0][:200] or "parse error"
    return None


JSON_CANDIDATES = 64
_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)


def find_json(text: str):
    """The first JSON object or array in `text`: in a code fence if there is
    one that holds JSON, else wherever one starts. Returns (found, value);
    at most JSON_CANDIDATES starting points are tried."""
    for m in _FENCE.finditer(text):
        if m.group(1).lower() not in ("", "json", "jsonc"):
            continue
        try:
            value = parse_json(m.group(2))
        except (ValueError, RecursionError):
            continue
        if isinstance(value, (dict, list)):
            return True, value
    decoder = json.JSONDecoder(**_STRICT)
    tried = 0
    for m in re.finditer(r"[\[{]", text):
        tried += 1
        if tried > JSON_CANDIDATES:
            break
        try:
            value, _ = decoder.raw_decode(text, m.start())
        except (ValueError, RecursionError):
            continue
        return True, value
    return False, None


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token. Documented as approximate;
    use only for guardrail assertions, never billing. An empty or blank text
    is no tokens at all (it used to count as one, so `min_tokens: 1` passed
    an empty reply)."""
    if not text.strip():
        return 0
    return max(1, len(text) // 4)


#: The JSON types the schema validator knows, and the Python-flavoured names
#: people write instead of them (for the hint in the error).
SCHEMA_TYPES = ("object", "array", "string", "number", "integer", "boolean",
                "null")
_TYPE_HINTS = {"str": "string", "text": "string", "int": "integer",
               "bool": "boolean", "float": "number", "double": "number",
               "dict": "object", "map": "object", "list": "array",
               "none": "null", "nil": "null"}
#: What the validator enforces. Any other keyword would pass every reply, so
#: it is an error (check_schema), except these notes that say nothing a
#: reply could break.
SCHEMA_KEYWORDS = ("type", "enum", "required", "properties", "items",
                   "additionalProperties", "minimum", "maximum",
                   "exclusiveMinimum", "exclusiveMaximum", "minLength",
                   "maxLength", "pattern", "minItems", "maxItems",
                   "uniqueItems", "const", "anyOf", "oneOf", "allOf")
_NUMBER_KEYS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
_COUNT_KEYS = ("minLength", "maxLength", "minItems", "maxItems")
_SCHEMA_NOTES = ("title", "description", "$schema", "$id", "$comment",
                 "default", "examples")

_TYPE_TESTS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def describe(value) -> str:
    """A value as a message names it: text in quotes, null as null, anything
    else by its type."""
    if isinstance(value, str):
        shown = repr(value)
        return shown if len(shown) <= 60 else shown[:57] + "..."
    return "null" if value is None else type(value).__name__


def check_schema(schema, path: str = "schema") -> None:
    """ValueError unless `schema` is one the validator can enforce. It checks
    type (one name or a list of them), enum, required, properties and items;
    a keyword beyond those (minimum, pattern, additionalProperties ...) would
    quietly pass every reply, so it is refused, as is a type it does not
    know (`str`, `int`: JSON says string, integer)."""
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: must be a mapping, got {describe(schema)}")
    for key in schema:
        if key not in SCHEMA_KEYWORDS and key not in _SCHEMA_NOTES:
            raise ValueError(
                f"{path}: '{key}' is not supported - this validator checks "
                f"{', '.join(SCHEMA_KEYWORDS)} and would pass every reply "
                f"whatever '{key}' says, so it is refused instead of "
                f"ignored (annotations such as title and description are "
                f"fine)")
    if "type" in schema:
        names = schema["type"]
        if isinstance(names, list):
            if not names:
                raise ValueError(f"{path}.type: needs at least one type")
        else:
            names = [names]
        for name in names:
            if not isinstance(name, str) or name not in SCHEMA_TYPES:
                hint = _TYPE_HINTS.get(name.lower()) \
                    if isinstance(name, str) else None
                raise ValueError(
                    f"{path}.type: unknown type {name!r} (use one of "
                    f"{', '.join(SCHEMA_TYPES)})"
                    + (f" - did you mean '{hint}'?" if hint else ""))
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise ValueError(f"{path}.enum: must be a list, got "
                         f"{describe(schema['enum'])}")
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or not all(
                isinstance(r, str) for r in required):
            raise ValueError(f"{path}.required: must be a list of key names "
                             f"(quote a name that looks like a number)")
    if "properties" in schema:
        props = schema["properties"]
        if not isinstance(props, dict):
            raise ValueError(f"{path}.properties: must be a mapping of key "
                             f"to schema, got {describe(props)}")
        for key, sub in props.items():
            check_schema(sub, f"{path}.properties.{key}")
    if "items" in schema:
        check_schema(schema["items"], f"{path}.items")
    if "additionalProperties" in schema:
        extra = schema["additionalProperties"]
        if not isinstance(extra, bool):
            check_schema(extra, f"{path}.additionalProperties")
    for key in _NUMBER_KEYS:
        if key in schema:
            v = schema[key]
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or v != v or v in (float("inf"), float("-inf"))):
                raise ValueError(f"{path}.{key}: must be a number, got "
                                 f"{describe(v)}")
    for key in _COUNT_KEYS:
        if key in schema:
            v = schema[key]
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"{path}.{key}: must be a whole number "
                                 f"(0 or more), got {describe(v)}")
    if "pattern" in schema:
        text = schema["pattern"]
        if not isinstance(text, str):
            raise ValueError(f"{path}.pattern: must be a regex text, got "
                             f"{describe(text)}")
        check_regex(f"{path}.pattern", text)
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise ValueError(f"{path}.uniqueItems: must be true or false, got "
                         f"{describe(schema['uniqueItems'])}")
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            subs = schema[key]
            if not isinstance(subs, list) or not subs:
                raise ValueError(f"{path}.{key}: must be a non-empty list "
                                 f"of schemas, got {describe(subs)}")
            for i, sub in enumerate(subs):
                check_schema(sub, f"{path}.{key}[{i}]")


def _same_json(a, b) -> bool:
    """Whether two JSON values are equal: true is no number (Python says
    True == 1, JSON does not), 1 is 1.0, and lists and objects are compared
    item by item the same way. Key order does not matter."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_json(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(_same_json, a, b))
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return False
    return a == b


#: A schema check stops collecting problems at this many (the report shows
#: three), so a big reply that is wrong everywhere is still fast.
MAX_ERRORS = 20


def _check_schema(data, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON-schema validator (see check_schema for what it checks).
    Returns list of error strings. required / properties apply to any object
    and items to any array, whether or not `type` is given beside them."""
    errors: list[str] = []
    names = schema.get("type")
    if names:
        names = names if isinstance(names, list) else [names]
        if not any(_TYPE_TESTS[n](data) for n in names):
            errors.append(f"{path}: expected {' or '.join(names)}, "
                          f"got {type(data).__name__}")
            return errors
    if "enum" in schema and not any(_same_json(data, member)
                                    for member in schema["enum"]):
        errors.append(f"{path}: {data!r} not in enum {schema['enum']}")
    if isinstance(data, dict):
        for req in schema.get("required", []):
            if req not in data:
                errors.append(f"{path}: missing required key {req!r}")
        for key, subschema in schema.get("properties", {}).items():
            if key in data:
                errors.extend(_check_schema(data[key], subschema, f"{path}.{key}"))
    if isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            errors.extend(_check_schema(item, schema["items"], f"{path}[{i}]"))
            if len(errors) >= MAX_ERRORS:      # a huge array: three are shown
                break
    if "const" in schema and not _same_json(data, schema["const"]):
        errors.append(f"{path}: {data!r} is not the required constant "
                      f"{schema['const']!r}")
    if isinstance(data, dict) and "additionalProperties" in schema:
        extra = schema["additionalProperties"]
        known = schema.get("properties", {})
        for key in data:
            if len(errors) >= MAX_ERRORS:
                break
            if key in known:
                continue
            if extra is False:
                errors.append(f"{path}: unexpected key {key!r}")
            elif extra is not True:
                errors.extend(_check_schema(data[key], extra,
                                            f"{path}.{key}"))
    if _is_number(data):
        for key, bad, word in (
                ("minimum", lambda v, m: v < m, "below the minimum"),
                ("maximum", lambda v, m: v > m, "above the maximum"),
                ("exclusiveMinimum", lambda v, m: v <= m,
                 "not above the exclusive minimum"),
                ("exclusiveMaximum", lambda v, m: v >= m,
                 "not below the exclusive maximum")):
            if key in schema and bad(data, schema[key]):
                errors.append(f"{path}: {data!r} is {word} {schema[key]!r}")
    if isinstance(data, str):
        if "minLength" in schema and len(data) < schema["minLength"]:
            errors.append(f"{path}: {len(data)} characters, minLength "
                          f"{schema['minLength']}")
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            errors.append(f"{path}: {len(data)} characters, maxLength "
                          f"{schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], data):
            errors.append(f"{path}: does not match pattern "
                          f"/{schema['pattern']}/")
    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            errors.append(f"{path}: {len(data)} items, minItems "
                          f"{schema['minItems']}")
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            errors.append(f"{path}: {len(data)} items, maxItems "
                          f"{schema['maxItems']}")
        if schema.get("uniqueItems"):
            seen = set()
            for item in data:
                k = _canon(item)
                if k in seen:
                    errors.append(f"{path}: items are not unique")
                    break
                seen.add(k)
    if "allOf" in schema:
        for sub in schema["allOf"]:
            errors.extend(_check_schema(data, sub, path))
    if "anyOf" in schema and all(_check_schema(data, sub, path)
                                 for sub in schema["anyOf"]):
        errors.append(f"{path}: matches none of the anyOf schemas")
    if "oneOf" in schema:
        hits = sum(1 for sub in schema["oneOf"]
                   if not _check_schema(data, sub, path))
        if hits != 1:
            errors.append(f"{path}: matches {hits} of the oneOf schemas, "
                          f"needs exactly 1")
    return errors


def _canon(v):
    """A hashable form of a JSON value that is equal exactly when
    _same_json says so (true is not 1, 1 is 1.0, key order is free)."""
    if isinstance(v, bool):
        return ("b", v)
    if isinstance(v, (int, float)):
        return ("n", v)
    if isinstance(v, str):
        return ("s", v)
    if v is None:
        return ("z",)
    if isinstance(v, list):
        return ("l", tuple(_canon(x) for x in v))
    return ("d", frozenset((k, _canon(x)) for k, x in v.items()))


def _schema_verdict(data, schema) -> tuple[bool, str]:
    try:
        errors = _check_schema(data, schema)
    except RecursionError:
        return False, "schema check: the JSON is nested too deeply"
    return (not errors,
            "schema valid" if not errors else "; ".join(errors[:3]))


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


TEXT_KINDS = ("contains", "not_contains", "icontains", "equals",
              "not_equals", "regex", "not_regex", "starts_with", "ends_with")
#: Take a list of texts: `contains_any: [refund, credit]`.
LIST_KINDS = ("contains_any", "contains_all")
COUNT_KINDS = ("max_tokens", "min_tokens", "max_chars", "min_chars",
               "max_words", "min_words", "max_lines")
#: Every assertion name. `judge` is put to a model by the runner; evaluate()
#: decides all the others itself.
SECURITY_KINDS = ("no_pii", "no_secrets", "no_hidden_chars", "no_urls",
                  "no_prompt_leak")
KINDS = (TEXT_KINDS + LIST_KINDS + ("json_valid", "schema", "contains_json",
                                    "is_xml")
         + COUNT_KINDS + SECURITY_KINDS + ("not", "judge"))
#: no_prompt_leak: words in a run, and the bounds on a value given.
LEAK_DEFAULT = 8
LEAK_MIN, LEAK_MAX = 3, 200
#: At most this many texts in one contains_any / contains_all.
MAX_LIST_ITEMS = 100


def as_text(value, where: str) -> str:
    """`value` as the text it stands for, or ValueError naming `where` (the
    key it came from). YAML turns `contains: 1` into an int - fine, that is
    the text "1", as written - but `contains: yes` into a boolean and
    `contains: ~` into null: those are almost never what was meant, so say so
    instead of searching for "True"."""
    text = miniyaml.scalar_text(value)
    if text is not None:
        return text           # a number is the text it was written as
    if isinstance(value, bool):
        raise ValueError(
            f"{where}: {value!r} is a YAML boolean (yes/no/true/false/on/off)"
            f" - quote the text you mean, e.g. {where}: \"yes\"")
    if value is None:
        raise ValueError(f"{where}: needs text, got null (empty or ~) - "
                         f"quote it, e.g. {where}: \"~\"")
    raise ValueError(f"{where}: needs text, got {type(value).__name__} "
                     f"{value!r}")


def _text(kind: str, want) -> str:
    """The text operand of a string assertion. Empty text is in every reply,
    so `contains: ""` would pass them all (and `not_contains: ""` fail them
    all): that is a mistake, not a test."""
    text = as_text(want, kind)
    if not text:
        raise ValueError(
            f"{kind}: is empty, so it would "
            + ("fail every reply - write the text that must not appear"
               if kind in ("not_contains", "not_regex") else
               "match every reply - write the text to look for"))
    return text


def _texts(kind: str, want) -> list[str]:
    """The list operand of contains_any / contains_all: one or more texts,
    none of them empty. An empty list would pass (all) or fail (any) every
    reply, so it is refused, like an empty `contains: ""`."""
    if not isinstance(want, list):
        raise ValueError(
            f"{kind}: needs a list of texts, e.g. {kind}: [refund, credit] "
            f"(got {describe(want)}) - for a single text use `contains`")
    if not want:
        raise ValueError(f"{kind}: is an empty list - write the texts to "
                         f"look for, e.g. {kind}: [refund, credit]")
    if len(want) > MAX_LIST_ITEMS:
        raise ValueError(f"{kind}: has {len(want)} texts; at most "
                         f"{MAX_LIST_ITEMS} are allowed (use a regex)")
    return [_text(f"{kind}[{i}]", item) for i, item in enumerate(want)]


def _flag(kind: str, want) -> bool:
    """The true / false operand of json_valid."""
    if isinstance(want, bool):
        return want
    raise ValueError(f"{kind}: needs true or false, got {want!r}")


def _count(kind: str, want) -> int:
    """The whole-number operand of a token-count assertion ("10" -> 10)."""
    number = None
    if isinstance(want, bool) or want is None:
        pass
    elif isinstance(want, int) or isinstance(want, float) and want.is_integer():
        number = int(want)
    elif isinstance(want, str) and re.fullmatch(r"[0-9]+", want.strip()):
        number = int(want.strip())
    if number is None or number < 0:
        raise ValueError(f"{kind}: needs a whole number (0 or more), "
                         f"got {want!r}")
    return number


def _security(kind: str, want):
    """The operand of a security assertion in working form."""
    if kind == "no_secrets":
        if want is True:
            return True
        raise ValueError("no_secrets: takes true, got " + describe(want))
    if kind == "no_hidden_chars":
        if want is True or want == "emoji_ok":
            return want
        raise ValueError("no_hidden_chars: takes true, or emoji_ok to allow "
                         "the zero-width joiner and emoji selector that "
                         "emoji need, got " + describe(want))
    if kind == "no_pii":
        if want is True:
            return list(scan.PII_DEFAULT)
        if (isinstance(want, list) and want
                and all(isinstance(k, str) and k in scan.PII_KINDS
                        for k in want)):
            return list(dict.fromkeys(want))
        raise ValueError(
            f"no_pii: takes true, or a list of kinds from "
            f"{', '.join(scan.PII_KINDS)}, got {describe(want)}")
    if kind == "no_urls":
        if want is True:
            return True
        if isinstance(want, list) and want:
            hosts = []
            for i, item in enumerate(want):
                host = _text(f"no_urls[{i}]", item).strip().lower().rstrip(".")
                if "/" in host or " " in host or "." not in host:
                    raise ValueError(
                        f"no_urls[{i}]: {item!r} is not a domain name - "
                        f"write the domain only, e.g. example.com")
                hosts.append(host)
            return list(dict.fromkeys(hosts))
        raise ValueError("no_urls: takes true (no links at all) or a list "
                         "of allowed domains, e.g. [example.com], got "
                         + describe(want))
    # no_prompt_leak
    if want is True:
        return LEAK_DEFAULT
    n = None
    if isinstance(want, int) and not isinstance(want, bool):
        n = want
    if n is None or not LEAK_MIN <= n <= LEAK_MAX:
        raise ValueError(f"no_prompt_leak: takes true ({LEAK_DEFAULT} "
                         f"words) or a number of words from {LEAK_MIN} to "
                         f"{LEAK_MAX}, got {describe(want)}")
    return n


try:                                    # Python 3.11 and later
    from re import _constants as _sre_c
    from re import _parser as _sre_parse
except ImportError:                     # pragma: no cover - 3.10
    import sre_constants as _sre_c
    import sre_parse as _sre_parse


def _items(body):
    """The nodes of a repeat's body, one group level looked through."""
    nodes = list(body)
    while len(nodes) == 1 and nodes[0][0] == _sre_c.SUBPATTERN:
        nodes = list(nodes[0][1][-1])
    return nodes


def _unbounded(node) -> bool:
    return (node[0] in (_sre_c.MAX_REPEAT, _sre_c.MIN_REPEAT)
            and node[1][1] == _sre_c.MAXREPEAT)


def _nested_repeat(nodes) -> bool:
    """True for a pattern that repeats, without limit, something that is
    itself only (or optionally) repeated without limit: (a+)+  (a*)*
    (\\w+\\s?)*  (a+b?)+ . On a reply that almost matches, these take time
    that doubles with every extra character."""
    for node in nodes:
        op, arg = node
        if op in (_sre_c.MAX_REPEAT, _sre_c.MIN_REPEAT):
            body = _items(arg[2])
            if (_unbounded(node) and any(_unbounded(n) for n in body)
                    and all(n[0] in (_sre_c.MAX_REPEAT, _sre_c.MIN_REPEAT)
                            for n in body)):
                # more than one mandatory piece (a+b+)+ splits only one way
                must = [n for n in body if n[1][0] >= 1]
                if len(must) <= 1 and all(_unbounded(n) for n in must):
                    return True
            if _nested_repeat(body):
                return True
        elif op == _sre_c.SUBPATTERN:
            if _nested_repeat(arg[-1]):
                return True
        elif op == _sre_c.BRANCH:
            if any(_nested_repeat(alt) for alt in arg[1]):
                return True
        elif op in (_sre_c.ASSERT, _sre_c.ASSERT_NOT):
            if _nested_repeat(arg[1]):
                return True
    return False


def check_regex(kind: str, text: str) -> re.Pattern:
    """Compile `text`, or ValueError: it does not compile, or it is the
    nested-repeat shape that can hang a run (Python's regex engine cannot
    be interrupted, and a reply is not under the author's control)."""
    try:
        compiled = re.compile(text)
    except (re.error, RecursionError, OverflowError) as e:
        raise ValueError(f"{kind}: invalid regex /{text}/: {e}") from None
    try:
        risky = _nested_repeat(_sre_parse.parse(text))
    except Exception:                    # an analysis that fails allows
        risky = False
    if risky:
        raise ValueError(
            f"{kind}: /{text}/ repeats a repeat without limit (like (a+)+ "
            f"or (\\w+\\s?)*): on a reply that almost matches it can run "
            f"for hours and cannot be interrupted, so it is refused - write "
            f"it without the inner repeat (a+ instead of (a+)+)")
    return compiled


def _pattern(kind: str, want) -> re.Pattern:
    return check_regex(kind, _text(kind, want))


def _rubric(value, where: str) -> str:
    if value is None:
        raise ValueError(f"{where}: needs a rubric, got nothing - say what "
                         f"the output must be to pass")
    text = as_text(value, where)
    if not text.strip():
        raise ValueError(f"{where}: is empty - say what the output must be "
                         f"to pass")
    return text


def _judge(want):
    """The operand of a judge: the rubric as text, or {rubric: text}."""
    if not isinstance(want, dict):
        return _rubric(want, "judge")
    if "rubric" not in want:
        found = ", ".join(repr(str(k)) for k in want) or "nothing"
        raise ValueError(f"judge: needs a 'rubric' (found only {found}) - "
                         f"write `judge: text`, or `rubric: text` under "
                         f"`judge:`")
    extra = [str(k) for k in want if k != "rubric"]
    if extra:
        raise ValueError(f"judge: unknown key {extra[0]!r} - a judge takes "
                         f"only 'rubric'")
    return {"rubric": _rubric(want["rubric"], "judge.rubric")}


def _schema_operand(kind: str, want):
    """A schema may be written as YAML, or pasted as JSON text (a block
    scalar: `schema: |` and the JSON under it), because the cases file reader
    has no flow maps and a schema is usually JSON already."""
    if isinstance(want, str):
        try:
            parsed = parse_json(want)
        except (ValueError, RecursionError) as e:
            raise ValueError(
                f"{kind}: must be a mapping, got {describe(want)} - write "
                f"the schema as YAML, one key per line, or as JSON text "
                f"({e})") from None
        if not isinstance(parsed, dict):
            raise ValueError(f"{kind}: must be a mapping; the JSON text is "
                             f"{describe(parsed)}, not an object")
        return parsed
    return want


def check(assertion) -> dict:
    """The assertion in working form - a text operand as text, a count as an
    int, a judge's rubric as text - or ValueError saying what is wrong with
    it (the key is named, the case and file are the caller's to add). Running
    it on its own result changes nothing."""
    if not isinstance(assertion, dict):
        raise ValueError(f"must be a mapping like `contains: text`, got "
                         f"{describe(assertion)}")
    if len(assertion) != 1:
        found = ", ".join(str(k) for k in assertion)
        raise ValueError(
            "is empty - write one `kind: value` pair" if not assertion else
            f"must have exactly one key, got {len(assertion)} ({found}) - "
            f"start a new list item with `- ` for each assertion")
    ((kind, want),) = assertion.items()
    if kind not in KINDS:
        close = difflib.get_close_matches(str(kind), KINDS, n=1)
        raise ValueError(
            f"unknown assertion {str(kind)!r}"
            + (f" (did you mean {close[0]!r}?)" if close else "")
            + f" - the assertions are {', '.join(KINDS)}")
    if kind in TEXT_KINDS:
        text = _text(kind, want)
        if kind in ("regex", "not_regex"):
            _pattern(kind, text)         # a pattern that cannot compile
        return {kind: text}
    if kind in LIST_KINDS:
        return {kind: _texts(kind, want)}
    if kind in COUNT_KINDS:
        return {kind: _count(kind, want)}
    if kind in ("json_valid", "is_xml"):
        return {kind: _flag(kind, want)}
    if kind == "schema":
        want = _schema_operand(kind, want)
        check_schema(want)
        return {kind: want}
    if kind == "contains_json":
        if want is True:
            return {kind: True}
        want = _schema_operand(kind, want) if isinstance(want, str) else want
        if isinstance(want, dict):
            check_schema(want)
            return {kind: want}
        raise ValueError("contains_json: takes true (a JSON object or array "
                         "somewhere in the reply) or a schema it must "
                         "satisfy, got " + describe(want))
    if kind == "not":
        if isinstance(want, dict) and len(want) == 1 and (
                next(iter(want)) in ("not", "judge")):
            raise ValueError(f"not: cannot wrap {next(iter(want))!r} "
                             + ("(a judge is a model's opinion)"
                                if next(iter(want)) == "judge" else
                                "(not-not is just the assertion)"))
        try:
            return {kind: check(want)}
        except ValueError as e:
            raise ValueError(f"not: {e}") from None
    if kind in SECURITY_KINDS:
        return {kind: _security(kind, want)}
    return {kind: _judge(want)}


def _evaluate_security(kind: str, want, output: str,
                       template: str | None) -> tuple[bool, str]:
    if kind == "no_secrets":
        found = scan.find_secrets(output)
        return (not found, "no secret-shaped text" if not found else
                "contains " + ", ".join(found) + " (value not shown)")
    if kind == "no_pii":
        found = scan.find_pii(output, want)
        return (not found, "no personal data of the kinds checked"
                if not found else "contains " + ", ".join(
                    f"{k} x{n}" for k, n in found.items())
                + " (value not shown)")
    if kind == "no_hidden_chars":
        found = scan.find_hidden(output, emoji_ok=(want == "emoji_ok"))
        return (not found, "no invisible characters" if not found else
                "contains invisible characters " + ", ".join(found[:5])
                + (f" and {len(found) - 5} more" if len(found) > 5 else ""))
    if kind == "no_urls":
        hosts = scan.find_urls(output)
        bad = hosts if want is True else [
            h for h in hosts if not scan.host_allowed(h, want)]
        bad = list(dict.fromkeys(bad))
        return (not bad, "no links" if want is True and not hosts else
                "no links outside the allowed domains" if not bad else
                "links to " + ", ".join(bad[:5])
                + (f" and {len(bad) - 5} more" if len(bad) > 5 else ""))
    # no_prompt_leak
    if template is None:
        raise ValueError("no_prompt_leak: needs the prompt text, which only "
                         "a run supplies (pass template= to evaluate)")
    gram = scan.find_prompt_leak(output, template, want)
    return (not gram, f"repeats no {want}-word run of the prompt" if not gram
            else f"repeats the prompt: {gram!r}")


def evaluate(assertion: dict, output: str,
             template: str | None = None) -> tuple[bool, str]:
    """Decide one assertion about a reply: (passed, detail). ValueError when
    the assertion itself cannot be used (see check). `template` is the
    prompt text (placeholders unfilled): only no_prompt_leak reads it."""
    kind, want = next(iter(check(assertion).items()))
    if kind in SECURITY_KINDS:
        return _evaluate_security(kind, want, output, template)

    if kind == "not":
        ok, detail = evaluate(want, output, template)
        return (not ok, f"not: {detail}" if not ok
                else f"unexpectedly holds: {detail}")
    if kind == "contains":
        ok = want in output
        return ok, f"contains {want!r}" if ok else f"missing {want!r}"
    if kind == "not_contains":
        ok = want not in output
        return ok, f"absent {want!r}" if ok else f"unexpectedly contains {want!r}"
    if kind == "icontains":
        ok = want.casefold() in output.casefold()
        return ok, (f"contains {want!r} (any case)" if ok
                    else f"missing {want!r} (any case)")
    if kind == "equals":
        ok = output.strip() == want.strip()
        return ok, ("equals the expected text" if ok
                    else f"expected exactly {want!r}")
    if kind == "not_equals":
        ok = output.strip() != want.strip()
        return ok, ("differs from the text" if ok
                    else f"unexpectedly equals {want!r}")
    if kind == "contains_any":
        found = [t for t in want if t in output]
        return (bool(found), f"contains {found[0]!r}" if found
                else f"contains none of {want!r}")
    if kind == "contains_all":
        missing = [t for t in want if t not in output]
        return (not missing, f"contains all {len(want)} texts" if not missing
                else f"missing {', '.join(repr(t) for t in missing[:5])}"
                + (f" and {len(missing) - 5} more" if len(missing) > 5
                   else ""))
    if kind == "regex":
        pat = _pattern(kind, want)
        ok = pat.search(output) is not None
        return ok, (f"matches /{pat.pattern}/" if ok
                    else f"no match for /{pat.pattern}/")
    if kind == "not_regex":
        pat = _pattern(kind, want)
        ok = pat.search(output) is None
        return ok, (f"no match for /{pat.pattern}/" if ok
                    else f"unexpected match /{pat.pattern}/")
    if kind == "starts_with":
        ok = output.startswith(want)
        return ok, "starts correctly" if ok else f"does not start with {want!r}"
    if kind == "ends_with":
        ok = output.rstrip().endswith(want)
        return ok, "ends correctly" if ok else f"does not end with {want!r}"
    if kind == "json_valid":
        wanted = _flag(kind, want)
        try:
            parse_json(output)
            problem = None
        except (ValueError, RecursionError) as e:   # nested too deep: no JSON
            problem = str(e)
        if wanted:
            return (True, "valid JSON") if problem is None else \
                (False, f"invalid JSON: {problem}")
        return (True, "not JSON, as required") if problem is not None else \
            (False, ("valid JSON, but json_valid: false asks for text that "
                     "is not"))
    if kind == "is_xml":
        wanted = _flag(kind, want)
        problem = xml_problem(output)
        if wanted:
            return (True, "well-formed XML") if problem is None else \
                (False, f"not well-formed XML: {problem}")
        return (True, "not XML, as required") if problem is not None else \
            (False, "well-formed XML, but is_xml: false asks for text that "
                    "is not")
    if kind == "schema":
        check_schema(want)
        try:
            data = parse_json(output)
        except (ValueError, RecursionError) as e:
            return False, f"schema check needs JSON, got: {e}"
        return _schema_verdict(data, want)
    if kind == "contains_json":
        found, data = find_json(output)
        if not found:
            return False, ("no JSON object or array found (a ```json fence "
                           "or a bare one)")
        if want is True:
            return True, "contains JSON"
        return _schema_verdict(data, want)
    if kind in ("max_chars", "min_chars", "max_words", "min_words",
                "max_lines"):
        text = output.strip()
        n, unit = ((len(text), "characters") if kind.endswith("chars") else
                   (len(text.split()), "words") if kind.endswith("words")
                   else (len(text.splitlines()), "lines"))
        ok = n <= want if kind.startswith("max") else n >= want
        return ok, f"{n} {unit} ({kind.split('_')[0]} {want})"
    if kind == "max_tokens":
        n = estimate_tokens(output)
        return n <= want, f"~{n} tokens (max {want})"
    if kind == "min_tokens":
        n = estimate_tokens(output)
        return n >= want, f"~{n} tokens (min {want})"
    raise ValueError(f"{kind}: is put to a model by the runner; it is not "
                     f"decided here")
