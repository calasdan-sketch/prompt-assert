"""Make untrusted text safe to show in a terminal, a CI log or an XML file.

A model's reply, a judge's reasoning, a server's error message, a case name
or a file name can carry characters that do something other than print:
ANSI escape sequences (recolour, move the cursor, rewrite earlier lines,
set the window title, OSC 8 hyperlinks), a carriage return that overwrites
the line it is on, bidirectional overrides that display text in a different
order than it is stored ("Trojan Source"), zero-width characters that hide
text, and lone surrogates. CI logs (GitHub Actions among them) render ANSI
escapes too, so a reply could fake a "3/3 passed" line or hide a FAIL.

clean() turns each such character into a visible escape (`\\x1b`,
`\\u202e`, `\\r`) and leaves everything else - letters in any script,
emoji, the report's own marks - as it is. Line feed and tab are kept: the
report is made of lines. The set is every character Unicode classes as a
control (Cc), a format character (Cf, which holds the bidi controls and the
zero-width characters), a surrogate (Cs) or a line / paragraph separator
(Zl, Zp); tests/test_synapse1.py checks it against unicodedata.
"""

from __future__ import annotations

import re

# Every Cc, Cf, Cs, Zl and Zp code point except tab and line feed, as ranges
# (generated from unicodedata; kept literal so importing costs nothing).
_RANGES = (
    (0x0, 0x8), (0xb, 0x1f), (0x7f, 0x9f), (0xad, 0xad), (0x600, 0x605),
    (0x61c, 0x61c), (0x6dd, 0x6dd), (0x70f, 0x70f), (0x890, 0x891),
    (0x8e2, 0x8e2), (0x180e, 0x180e), (0x200b, 0x200f), (0x2028, 0x202e),
    (0x2060, 0x2064), (0x2066, 0x206f), (0xd800, 0xdfff), (0xfeff, 0xfeff),
    (0xfff9, 0xfffb), (0x110bd, 0x110bd), (0x110cd, 0x110cd),
    (0x13430, 0x1343f), (0x1bca0, 0x1bca3), (0x1d173, 0x1d17a),
    (0xe0001, 0xe0001), (0xe0020, 0xe007f),
)
# Not characters in XML 1.0, whatever escaping is used: an XML report must
# not contain them at all (clean() already escapes the rest of this set).
_XML_EXTRA = ((0xfffe, 0xffff),)


def _class(ranges) -> re.Pattern:
    parts = []
    for lo, hi in ranges:
        parts.append(re.escape(chr(lo)) if lo == hi
                     else f"{re.escape(chr(lo))}-{re.escape(chr(hi))}")
    return re.compile("[" + "".join(parts) + "]")


_UNSAFE = _class(_RANGES)
_UNSAFE_XML = _class(_RANGES + _XML_EXTRA)
_NAMED = {"\r": "\\r", "\x0b": "\\v", "\x0c": "\\f", "\x00": "\\0"}


def _escape(m: re.Match) -> str:
    ch = m.group()
    if ch in _NAMED:
        return _NAMED[ch]
    cp = ord(ch)
    if cp <= 0xff:
        return f"\\x{cp:02x}"
    if cp <= 0xffff:
        return f"\\u{cp:04x}"
    return f"\\U{cp:08x}"


def clean(text: str) -> str:
    """`text` with every control, format, surrogate and separator character
    (bar line feed and tab) shown as an escape instead of acting."""
    if not isinstance(text, str):
        text = str(text)
    return _UNSAFE.sub(_escape, text)


def clean_xml(text: str) -> str:
    """clean(), plus the two code points XML 1.0 cannot hold at all. The
    result is always legal XML character data once &, < and > are escaped
    (ElementTree does that)."""
    if not isinstance(text, str):
        text = str(text)
    return _UNSAFE_XML.sub(_escape, text)
