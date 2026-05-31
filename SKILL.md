---
name: zan-punct-fix
description: >
  Audits and fixes incorrect Chinese punctuation across every file in a folder
  — the classic artifact of AI-generated content, where half-width marks like
  "," "." "?" "!" ":" ";" straight quotes "" and parens () get used where the
  full-width Chinese forms "，" "。" "？" "！" "：" "；" "“”" "（）" belong. Use
  this skill whenever the user wants to check, review, clean up, normalize, or
  fix punctuation (especially Chinese / 中文标点符号) in documents, markdown,
  code comments, docx, or a whole directory — even if they just say something
  like "标点符号有问题", "帮我检查标点", "the commas look wrong", or "clean up
  this folder's punctuation". It scans text, code, and docx files; protects
  code, URLs, English sentences, and numbers; and reports every change.
---

# Chinese Punctuation Fixer

## Why this exists

AI models routinely emit Chinese prose with **half-width punctuation** because
the model slips into ASCII out of habit. The reader sees `这是测试,对吧.` instead
of the correct `这是测试，对吧。`. It looks sloppy and unprofessional. The job of
this skill is to sweep a file or folder and put the **full-width** marks back
where Chinese text demands them — without disturbing English, code, URLs, or
numbers, where half-width marks are correct.

The hard part isn't the replacement, it's the **context**. A comma between two
digits (`1,000`) is a thousands separator and must stay. A period in `Node.js`
or `3.14` must stay. A quote that delimits a Python string must stay straight.
The bundled script encodes these judgments so you don't have to eyeball
thousands of characters by hand.

## The workflow

Run the script. It does the heavy lifting; you supervise and handle the
genuinely ambiguous leftovers.

1. **Preview first (dry run).** Show the user what *would* change before
   touching anything. This builds trust and catches surprises.

   ```bash
   python3 <skill-dir>/scripts/fix_punctuation.py <target-path> --dry-run
   ```

2. **Read the report.** Look at the before/after for each line. If something
   looks wrong (a false positive — a mark that should have stayed half-width),
   note it. The common offenders are listed under "Judgment calls" below.

3. **Apply the fixes.** Drop `--dry-run` to write the changes in place. Add
   `--json report.json` if you want a machine-readable record of every change.

   ```bash
   python3 <skill-dir>/scripts/fix_punctuation.py <target-path> --json /tmp/punct-report.json
   ```

4. **Summarize for the user.** Tell them how many issues were fixed across how
   many files, and call out anything you deliberately left alone or that needs
   their eyes.

If the target is under version control, mention that the user can `git diff` to
review — that's the most natural way for them to verify a bulk edit.

## What the script fixes

It converts a half-width mark to its full-width Chinese form **only when the
mark sits in Chinese context** — i.e. a CJK character is its neighbor.

| Half-width | Full-width | Triggers when |
|-----------|-----------|----------------|
| `,` | `，` | adjacent to a CJK char (not between digits) |
| `,` | `、` (顿号) | separating **3+ short CJK-only list items** (产品,渠道,服务) |
| `.` | `。` | the preceding real char is CJK (not a decimal/version/ellipsis) |
| `?` `!` | `？` `！` | adjacent to a CJK char |
| `:` | `：` | the preceding real char is CJK (not a time like `12:30`) |
| `;` | `；` | adjacent to a CJK char |
| `"..."` | `“...”` | the pair surrounds or sits beside Chinese text *(prose only)* |
| `'...'` | `‘...’` | the pair's content contains Chinese *(prose only)* |
| `(...)` | `（...）` | the pair surrounds or sits beside Chinese text *(prose only)* |
| `...` | `……` | adjacent to a CJK char (3+ dots) |
| `--` `—` | `——` | adjacent to a CJK char |
| `　` (full-width space) | ` ` | always normalized to a normal space |

A sentence-ending mark is also fixed when it sits **after a closing quote or
bracket** whose content is Chinese — `他说"对".` becomes `他说“对”。`, because the
script looks through the closer to find the real Chinese character behind it.

## What it deliberately protects

These are the false-positive traps. The script masks them out before doing
anything, so they pass through untouched:

- **Fenced code blocks** ```` ``` ```` / `~~~`, and **inline code** `` `...` ``
- **URLs**, **emails**, and **file paths** (`a/b/c.ext`)
- **HTML / XML tags** `<div>`
- **Numbers**: `1,000` (thousands), `3.14` (decimals), `12:30` (time)
- **English sentences**: with no CJK neighbor, nothing converts, so pure-English
  text is left exactly as written
- **Source-code syntax**: in code files (`.py`, `.js`, `.ts`, `.go`, `.json`,
  etc.) the script fixes punctuation *inside* Chinese strings and comments but
  leaves **straight quotes and parens alone** — converting a `"` string
  delimiter to `“` or a `(` to `（` would break the code. Sentence punctuation
  inside a Chinese string (e.g. `"你好，世界"`) is still fixed because it's
  content, not syntax.

## The review pass — what the script can't decide alone

Some punctuation choices depend on **meaning**, not adjacency, so no regex can
get them right every time. The script intentionally stays conservative on these
and leaves them for you. After applying the script, **read the diff and apply
judgment** to the following — this is where you add value the script can't:

- **逗号 vs 顿号 (`，` vs `、`).** The script converts a comma to 顿号 only for
  runs of 3+ short Chinese-only items, and backs off if a segment opens with a
  clause word (因为/所以/但是…). It will still miss cases in both directions:
  a two-item list (`苹果,香蕉` → it leaves `，`, but `、` may read better) or a
  three-clause sentence that happens to look like a list. Skim list-like spots
  and adjust. When unsure, `，` is the safe default — it's never *wrong*, just
  sometimes less polished than `、`.
- **A `？` that isn't really a question.** `有问题找管理员？` is a statement, not
  a question — it should end in `。`. The script can't read intent, so it keeps
  `？` wherever a question mark stood. Change rhetorical/declarative ones to `。`.
- **A period after a Latin abbreviation that ends a Chinese sentence**, e.g.
  `用的是 React.` — the script leaves a `.` whose previous real char is Latin
  alone. If it clearly ends a Chinese sentence, change it to `。`.
- **Mismatched or deeply nested parens/quotes** in messy prose — the script
  handles one level of pairing; unbalanced marks may need a human.

The division of labor is the point: the **script** does the high-volume,
unambiguous conversions identically every time and never touches code/URLs/
numbers; **you** handle the handful of meaning-dependent calls it flags. If the
user reports a *systematic* false positive (not a one-off judgment call), prefer
fixing the **script's rule** over hand-editing — that fix then helps at scale.

## Options

```
--dry-run             Preview without writing. Always start here.
--json <file>         Write a full machine-readable change report.
--no-dash             Don't normalize "--" / "—" into "——".
--no-fullwidth-space  Leave full-width spaces (　) as-is.
--no-dunhao           Don't turn short comma lists (产品,渠道,服务) into 顿号 (、).
--include-all         Scan .git / node_modules / build dirs too (skipped by default).
```

By default the scan **skips** version-control and dependency directories
(`.git`, `node_modules`, `.venv`, `dist`, `build`, …) and binary file types
(images, archives, fonts, office binaries other than `.docx`). It walks
everything else recursively — nothing in scope is omitted.

## .docx files

The script edits Word documents directly: it rewrites the text inside the
document's `<w:t>` runs (body, headers, footers) and repackages the `.docx`,
leaving all formatting intact. The same context rules apply. Always `--dry-run`
first so the user can confirm before a binary file is rewritten in place.

## A note on scope

If the user points the skill at a large tree, the dry run can produce a long
report. Summarize patterns ("most fixes were missing full-width commas in the
docs/ folder") rather than dumping every line, and offer to apply.
