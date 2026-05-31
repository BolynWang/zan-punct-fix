#!/usr/bin/env python3
"""Context-aware Chinese punctuation fixer.

Walks a path (file or folder), finds half-width punctuation that is being used
in a Chinese context, and rewrites it to the correct full-width form -- while
leaving code, URLs, English sentences, and numbers untouched.

The whole point is to fix the most common artifact of AI-generated Chinese
content: a half-width "," "." "?" "!" ":" ";" or straight quote sitting right
next to Chinese characters where a full-width "，" "。" "？" "！" "：" "；" or
curly quote belongs.

Usage:
    python fix_punctuation.py <path> [--dry-run] [--json report.json]
                                     [--no-dash] [--no-fullwidth-space]
                                     [--include-all]

--dry-run             Report what would change without writing anything.
--json <file>         Also write a machine-readable report to <file>.
--no-dash             Skip normalizing "--" / "—" runs into "——".
--no-fullwidth-space  Leave full-width spaces (U+3000) alone.
--no-dunhao           Don't turn short comma lists (产品,渠道,服务) into 顿号 (、).
--include-all         Don't skip .git / node_modules / build dirs.
"""

import argparse
import json
import os
import re
import sys
import zipfile
import shutil
import tempfile

# ---------------------------------------------------------------------------
# Character classification
# ---------------------------------------------------------------------------

def is_cjk(ch):
    """A character that signals 'we are in Chinese text'.

    Includes CJK ideographs, compatibility ideographs, CJK symbols/punctuation
    (so existing full-width punctuation counts as Chinese context), and the
    full-width / half-width forms block.
    """
    if not ch:
        return False
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF or   # CJK Unified Ideographs
        0x3400 <= o <= 0x4DBF or   # Extension A
        0xF900 <= o <= 0xFAFF or   # Compatibility Ideographs
        0x3000 <= o <= 0x303F or   # CJK Symbols and Punctuation
        0xFF00 <= o <= 0xFFEF       # Halfwidth and Fullwidth Forms
    )


CJK_RE = re.compile(r'[㐀-䶿一-鿿豈-﫿]')


def prev_nonspace(text, i):
    j = i - 1
    while j >= 0 and text[j] in ' \t':
        j -= 1
    return text[j] if j >= 0 else ''


def next_nonspace(text, i):
    j = i + 1
    n = len(text)
    while j < n and text[j] in ' \t':
        j += 1
    return text[j] if j < n else ''


# ---------------------------------------------------------------------------
# Masking: protect spans we must never touch
# ---------------------------------------------------------------------------

# Private-use sentinels; files containing these raw are virtually nonexistent,
# and we skip binary files anyway.
MASK_OPEN = ''
MASK_CLOSE = ''

# Order matters: fenced code first (multiline), then inline code, then the rest.
MASK_PATTERNS = [
    re.compile(r'```.*?```', re.DOTALL),          # fenced code block ```
    re.compile(r'~~~.*?~~~', re.DOTALL),          # fenced code block ~~~
    re.compile(r'`[^`\n]+`'),                       # inline code
    re.compile(r'<[^>\n]+>'),                        # html / xml tags
    re.compile(r'https?://[^\s<>"　)]+'),     # urls
    re.compile(r'\bwww\.[^\s<>"　)]+'),        # bare www urls
    re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'),        # emails
    re.compile(r'[A-Za-z0-9_.\-]+/[A-Za-z0-9_./\-]+'),  # paths a/b/c.ext
]


def mask(text):
    """Replace protected spans with sentinel tokens; return (masked, store)."""
    store = []

    def stash(m):
        idx = len(store)
        store.append(m.group(0))
        return f'{MASK_OPEN}{idx}{MASK_CLOSE}'

    for pat in MASK_PATTERNS:
        text = pat.sub(stash, text)
    return text, store


def unmask(text, store):
    def restore(m):
        return store[int(m.group(1))]
    return re.sub(MASK_OPEN + r'(\d+)' + MASK_CLOSE, restore, text)


# ---------------------------------------------------------------------------
# The punctuation rules
# ---------------------------------------------------------------------------

# Sentence punctuation that becomes full-width when adjacent to Chinese.
PAIR_FULL = {',': '，', ';': '；', ':': '：', '!': '！', '?': '？'}

# Closing marks that a sentence-ending punctuation can sit *after* while still
# belonging to the Chinese sentence: e.g. 他说“好”。 or （注）。 — the real
# content char is before the closer, so we look through these.
#
# IMPORTANT: only FULL-WIDTH / CJK closers belong here, never straight ASCII
# " ' ) . In prose, quotes/parens are converted to full-width *before* the
# char-by-char pass, so the closer we need to see through is already full-width.
# In source code the straight delimiters stay as-is, and we must NOT look
# through them — otherwise `"值";` would see the 值 inside and wrongly turn the
# trailing ; . , into full-width, breaking the code.
CLOSERS = '”’）」』》】〉〕｝'

# A run of CJK characters, for enumeration (顿号) detection.
CJK_CLASS = r'[一-鿿㐀-䶿豈-﫿]'

# Words that open a clause rather than a list item. If a comma-separated run
# contains one, it's reasoning/narration, not a parallel list, so we keep 逗号.
CLAUSE_STARTERS = ('因为', '所以', '但是', '可是', '不过', '然后', '因此', '于是',
                   '虽然', '如果', '而且', '并且', '接着', '同时', '由于', '除非',
                   '使得', '导致', '从而')


def prev_content_char(text, i):
    """Previous meaningful char, skipping spaces AND closing quotes/brackets.

    This is what lets 他说“对”。 get a full-width 。 — the char that gives the
    sentence its language is 对, sitting behind the closing quote.
    """
    j = i - 1
    while j >= 0 and (text[j] in ' \t' or text[j] in CLOSERS):
        j -= 1
    return text[j] if j >= 0 else ''


def fix_text(text, do_dash=True, do_fw_space=True, do_quotes=True,
             do_parens=True, do_dunhao=True):
    """Return text with Chinese-context punctuation normalized.

    Operates on already-masked text so code/urls/etc. are inert sentinels.

    do_quotes / do_parens default on for prose. Turn them off for source
    code, where a straight quote is usually a string delimiter and a paren is
    usually syntax -- converting those to full-width would break the code,
    whereas a comma or period inside a Chinese string/comment is just content
    and is always safe to fix.

    do_dunhao turns short comma-separated Chinese items (产品,渠道,服务) into an
    enumeration with 顿号 (产品、渠道、服务), which is the typographically correct
    separator for parallel list items. It only fires on runs of 3+ short
    CJK-only items, because list-vs-clause is genuinely ambiguous for two
    items or longer segments -- those stay 逗号 and are left for the human /
    LLM review pass described in SKILL.md.
    """
    # --- ellipsis: 3+ dots next to Chinese -> ……
    def ell(m):
        s, e = m.start(), m.end()
        if is_cjk(prev_nonspace(text, s)) or is_cjk(next_nonspace(text, e - 1)):
            return '……'
        return m.group(0)
    text = re.sub(r'\.{3,}', ell, text)

    # --- enumeration: 短CJK项,短CJK项,短CJK项 -> 顿号 、
    # Require at least 3 items (two commas) and short CJK-only items, so we
    # don't turn comma-separated clauses into a 顿号 list. The trailing review
    # pass (LLM) is the safety net for the cases this can't disambiguate.
    if do_dunhao:
        enum = re.compile(CJK_CLASS + r'{1,6}(?:,' + CJK_CLASS + r'{1,6}){2,}')

        def enum_repl(m):
            items = m.group(0).split(',')
            # If a segment opens with a clause connector, this is reasoning /
            # narration (苹果,香蕉,因为健康), not a parallel list -- leave commas.
            if any(it.startswith(CLAUSE_STARTERS) for it in items):
                return m.group(0)
            return m.group(0).replace(',', '、')
        text = enum.sub(enum_repl, text)

    if do_quotes:
        # --- straight double quotes -> curly, when the pair lives in Chinese text
        def dq(m):
            inner = m.group(1)
            before = text[m.start() - 1] if m.start() > 0 else ''
            after = text[m.end()] if m.end() < len(text) else ''
            if is_cjk(before) or is_cjk(after) or CJK_RE.search(inner):
                return '“' + inner + '”'
            return m.group(0)
        text = re.sub(r'"([^"\n]*)"', dq, text)

        # --- straight single quotes -> curly, only when clearly Chinese (inner CJK)
        def sq(m):
            inner = m.group(1)
            if CJK_RE.search(inner):
                return '‘' + inner + '’'
            return m.group(0)
        text = re.sub(r"'([^'\n]*)'", sq, text)

    if do_parens:
        # --- parentheses: convert the WHOLE pair so they never mismatch.
        # A pair belongs to Chinese text if its content or its immediate
        # surroundings are Chinese; then both brackets go full-width together.
        def paren(m):
            inner = m.group(1)
            before = text[m.start() - 1] if m.start() > 0 else ''
            after = text[m.end()] if m.end() < len(text) else ''
            if is_cjk(before) or is_cjk(after) or CJK_RE.search(inner):
                return '（' + inner + '）'
            return m.group(0)
        text = re.sub(r'\(([^()\n]*)\)', paren, text)

    # --- character-by-character pass for the rest
    out = []
    n = len(text)
    for i, c in enumerate(text):
        if c in PAIR_FULL:
            prev_c = text[i - 1] if i > 0 else ''
            next_c = text[i + 1] if i + 1 < n else ''
            pc = prev_content_char(text, i)   # sees through closing quotes/brackets
            nn = next_nonspace(text, i)

            # comma / colon inside numbers (1,000  12:30) stay half-width
            if c in ',:' and prev_c.isdigit() and next_c.isdigit():
                out.append(c)
                continue
            # colon attaches to what precedes it (label:)
            if c == ':':
                out.append('：' if is_cjk(pc) else c)
                continue
            # comma / ; / ! / ? : Chinese on either side -> full width
            out.append(PAIR_FULL[c] if (is_cjk(pc) or is_cjk(nn)) else c)
            continue

        if c == '.':
            prev_c = text[i - 1] if i > 0 else ''
            next_c = text[i + 1] if i + 1 < n else ''
            # decimals / version numbers / ellipsis fragments stay
            if prev_c.isdigit() and next_c.isdigit():
                out.append(c)
                continue
            if prev_c == '.' or next_c == '.':
                out.append(c)
                continue
            # a period closing a clause whose last real char is Chinese
            # (looking through a closing quote/bracket: 他说“对”. -> 。)
            out.append('。' if is_cjk(prev_content_char(text, i)) else c)
            continue

        out.append(c)

    text = ''.join(out)

    # --- dash runs: "--" or "—" next to Chinese -> "——"
    if do_dash:
        def dash(m):
            s, e = m.start(), m.end()
            if is_cjk(prev_nonspace(text, s)) or is_cjk(next_nonspace(text, e - 1)):
                return '——'
            return m.group(0)
        text = re.sub(r'-{2,}|—+', dash, text)

    # --- full-width space between content -> normal space
    if do_fw_space:
        text = re.sub(r'　', ' ', text)

    return text


def process_string(text, **kw):
    """Mask -> fix -> unmask. Returns fixed text."""
    masked, store = mask(text)
    fixed = fix_text(masked, **kw)
    return unmask(fixed, store)


# ---------------------------------------------------------------------------
# Diffing for the report
# ---------------------------------------------------------------------------

def line_changes(original, fixed):
    """Return list of {line, before, after} for lines that differ."""
    o_lines = original.splitlines()
    f_lines = fixed.splitlines()
    changes = []
    for idx, (o, f) in enumerate(zip(o_lines, f_lines), start=1):
        if o != f:
            changes.append({'line': idx, 'before': o, 'after': f})
    return changes


# ---------------------------------------------------------------------------
# File handling
# ---------------------------------------------------------------------------

SKIP_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__',
             'dist', 'build', '.next', '.idea', '.vscode', 'target'}

# Source code: fix punctuation inside Chinese strings/comments, but DON'T touch
# straight quotes or parens (they're string delimiters / syntax there).
CODE_EXT = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.c', '.h', '.cpp', '.hpp',
    '.cc', '.cs', '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.scala',
    '.sh', '.bash', '.zsh', '.lua', '.pl', '.r', '.sql', '.vue', '.dart',
    '.json', '.yaml', '.yml', '.toml', '.xml', '.css', '.scss', '.less',
}

# Extensions we know are binary / not worth scanning as text.
BINARY_EXT = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.svg',
    '.pdf', '.zip', '.gz', '.tar', '.7z', '.rar', '.mp3', '.mp4', '.mov',
    '.avi', '.wav', '.flac', '.ttf', '.otf', '.woff', '.woff2', '.eot',
    '.so', '.dll', '.dylib', '.exe', '.bin', '.class', '.pyc', '.o', '.a',
    '.xlsx', '.xls', '.pptx', '.ppt', '.key', '.numbers', '.pages',
}


def read_text(path):
    """Return (text, encoding) or (None, None) if binary/unreadable."""
    with open(path, 'rb') as f:
        raw = f.read()
    if b'\x00' in raw[:8192]:
        return None, None
    for enc in ('utf-8', 'utf-8-sig', 'gb18030'):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return None, None


def process_docx(path, dry_run, **kw):
    """Fix punctuation inside a .docx's text nodes. Returns list of changes."""
    changes = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        targets = [n for n in names if n.endswith('document.xml')
                   or re.match(r'word/(header|footer)\d*\.xml$', n)]
        new_data = {}
        for n in targets:
            xml = z.read(n).decode('utf-8')
            def repl(m):
                before = m.group(2)
                after = process_string(before, **kw)
                if after != before:
                    changes.append({'part': n, 'before': before, 'after': after})
                return m.group(1) + after + m.group(3)
            # <w:t ...>text</w:t> -- only the inner text
            new_xml = re.sub(r'(<w:t[^>]*>)(.*?)(</w:t>)', repl, xml, flags=re.DOTALL)
            if new_xml != xml:
                new_data[n] = new_xml
        if new_data and not dry_run:
            _rewrite_zip(path, new_data, names, z)
    return changes


def _rewrite_zip(path, new_data, names, zin):
    fd, tmp = tempfile.mkstemp(suffix='.docx')
    os.close(fd)
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            data = new_data[n].encode('utf-8') if n in new_data else zin.read(n)
            zout.writestr(n, data)
    shutil.move(tmp, path)


def iter_files(root, include_all):
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        if not include_all:
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            yield os.path.join(dirpath, fn)


def main():
    ap = argparse.ArgumentParser(description='Fix Chinese punctuation in files.')
    ap.add_argument('path', help='File or directory to process.')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--json', metavar='FILE', help='Write JSON report to FILE.')
    ap.add_argument('--no-dash', action='store_true')
    ap.add_argument('--no-fullwidth-space', action='store_true')
    ap.add_argument('--no-dunhao', action='store_true',
                    help="Don't turn short comma lists (产品,渠道,服务) into 顿号 (、).")
    ap.add_argument('--include-all', action='store_true')
    args = ap.parse_args()

    kw = dict(do_dash=not args.no_dash, do_fw_space=not args.no_fullwidth_space,
              do_dunhao=not args.no_dunhao)

    report = {'files': [], 'skipped': [], 'total_changes': 0}

    for path in iter_files(args.path, args.include_all):
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == '.docx':
                changes = process_docx(path, args.dry_run, **kw)
                if changes:
                    report['files'].append({'path': path, 'count': len(changes),
                                            'changes': changes})
                    report['total_changes'] += len(changes)
                continue
            if ext in BINARY_EXT:
                report['skipped'].append({'path': path, 'reason': 'binary ext'})
                continue
            text, enc = read_text(path)
            if text is None:
                report['skipped'].append({'path': path, 'reason': 'binary/unreadable'})
                continue
            fkw = dict(kw)
            if ext in CODE_EXT:
                fkw['do_quotes'] = False
                fkw['do_parens'] = False
            fixed = process_string(text, **fkw)
            if fixed != text:
                changes = line_changes(text, fixed)
                report['files'].append({'path': path, 'encoding': enc,
                                        'count': len(changes), 'changes': changes})
                report['total_changes'] += len(changes)
                if not args.dry_run:
                    with open(path, 'w', encoding=enc) as f:
                        f.write(fixed)
        except Exception as e:  # keep going; one bad file shouldn't stop the run
            report['skipped'].append({'path': path, 'reason': f'error: {e}'})

    # human-readable summary to stdout
    verb = 'Would fix' if args.dry_run else 'Fixed'
    print(f'{verb} {report["total_changes"]} punctuation issue(s) '
          f'across {len(report["files"])} file(s).')
    for f in report['files']:
        print(f'\n  {f["path"]}  ({f["count"]} change(s))')
        for ch in f.get('changes', [])[:40]:
            if 'line' in ch:
                print(f'    L{ch["line"]}: {ch["before"]!r}')
                print(f'         -> {ch["after"]!r}')
            else:
                print(f'    [{ch.get("part","docx")}] {ch["before"]!r} -> {ch["after"]!r}')
        if f['count'] > 40:
            print(f'    ... and {f["count"] - 40} more')
    if report['skipped']:
        print(f'\nSkipped {len(report["skipped"])} file(s) (binary/unreadable).')

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as jf:
            json.dump(report, jf, ensure_ascii=False, indent=2)
        print(f'\nJSON report written to {args.json}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
