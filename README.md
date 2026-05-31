# zan-punct-fix

A Claude Code / agent **skill** that audits and fixes incorrect Chinese
punctuation across every file in a folder — the classic artifact of
AI-generated content, where half-width marks (`,` `.` `?` `!` `:` `;`, straight
quotes, parens) get used where the full-width Chinese forms
(`，` `。` `？` `！` `：` `；` `“”` `（）`) belong.

## What it does

Recursively scans a path (text, code, and `.docx` files), converts half-width
punctuation to the correct full-width form **only when it sits in Chinese
context**, and reports every change.

| Half-width | Full-width | When |
|---|---|---|
| `,` | `，` | next to a CJK char (not between digits) |
| `,` | `、` (顿号) | separating 3+ short CJK list items (产品,渠道,服务) |
| `.` | `。` | preceding real char is CJK (not a decimal/version) |
| `? !` | `？！` | next to a CJK char |
| `: ;` | `：；` | preceding char is CJK (not a time like `12:30`) |
| `"…"` `'…'` `(…)` | `“…”` `‘…’` `（…）` | pair sits in Chinese text (prose only) |
| `...` `-- —` | `……` `——` | next to a CJK char |

### What it protects (never touched)

- Fenced/inline code, URLs, emails, file paths, HTML tags
- Numbers: `1,000` (thousands), `3.14` (decimals), `12:30` (time)
- Pure-English sentences (no CJK neighbor → no change)
- **Source-code syntax**: in `.py`/`.js`/`.json`/… it fixes punctuation *inside*
  Chinese strings and comments but leaves string-delimiter quotes and parens
  alone, so it never breaks code.

## Usage

```bash
# Preview (recommended first)
python3 scripts/fix_punctuation.py <path> --dry-run

# Apply + write a JSON report
python3 scripts/fix_punctuation.py <path> --json report.json
```

Options: `--dry-run`, `--json FILE`, `--no-dash`, `--no-fullwidth-space`,
`--no-dunhao`, `--include-all`. See `SKILL.md` for the full behavior spec and
the meaning-dependent cases that are left for a human/LLM review pass.

## Install as a skill

Clone into your skills directory:

```bash
git clone git@github.com:BolynWang/zan-punct-fix.git ~/.claude/skills/zan-punct-fix
```

The agent triggers it on requests like “帮我检查这个文件夹的标点符号”.
