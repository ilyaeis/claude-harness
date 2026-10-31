# my-project: Claude context

Replace this line with 2-3 sentences: what the project does, and who uses it.

## Commands

```bash
make check        # the quality gate: ruff check, ruff format --check, tools/loc_check.py, pytest
make fix          # ruff fixes what it can, then formats
pytest tests/     # tests only
```

## Test-driven development

Work in small red-green-refactor steps.

1. Write a test that fails. Run it. Read the failure. It must fail for the reason you expect.
2. Write the smallest code that makes the test pass. Run the test again.
3. Refactor. Make the names and the structure clear. The tests stay green.
4. Repeat for the next behaviour.

Rules:
- Every bug fix starts with a test that shows the bug. The test fails before the fix and passes after it.
- Test behaviour through the public functions. Do not test private helpers directly.
- One test checks one behaviour. The test name says the behaviour: `test_top_words_rejects_count_below_one`.
- Do not delete or weaken a test to make the suite pass. If a test is wrong, say why in the commit message.
- Run `make check` before every commit. The commit hook runs it too, and blocks the commit when it fails.
- Never use `--no-verify`. The hook blocks it.

## Code quality gate

`scripts/check.sh` runs four steps. All must pass.

| Step | What it blocks |
|---|---|
| `ruff check .` | lint errors, likely bugs, import order, complexity over 10 (`C901`) |
| `ruff format --check .` | code that is not formatted |
| `tools/loc_check.py` | a file over 300, a class over 200, a function over 60 lines of code |
| `pytest` | a failing test |

Limits live in `pyproject.toml` (`[tool.ruff]`, `[tool.loc_check]`).
For agents, `.claude/hooks/check_before_commit.py` runs the gate before `git commit` and `gh pr create`.

## Documentation Maintenance

Do not add README files in code folders. Put the facts in the code: names, types and docstrings.
When a README contradicts the code, the code wins. Update or delete the README in the same change.

## Code readability rules

Goal: a person reads a module top to bottom like a short story. Each function says one thing. Names and types tell what happens, so comments only tell why.

Scope: apply these rules to code that you add or change.

### 1. Story order

- Every module starts with a docstring of 1-3 sentences: what it does and who calls it.
- Put the public entry function first. Put its helpers below it, in the order it calls them.
- A top-level function reads as a list of steps. Each step is one call with a verb name. No step mixes high-level flow with low-level detail.

```python
def build_invoice(order: Order, options: InvoiceOptions) -> Invoice:
    lines = price_order_lines(order)
    totals = sum_totals(lines, options.tax_rate)
    document = render_invoice(lines, totals)
    return save_invoice(document, options.output_dir)
```

### 2. Functions

- One job per function. Aim for 30 lines of code or fewer. `tools/loc_check.py` blocks more than 60 (blank lines, comments and docstrings do not count).
- At most 4 parameters. Group more into a typed options object.
- No boolean parameter that switches what a function does. Write two functions, or take a `Literal` / `StrEnum` mode.
- Use guard clauses and return early. Nest at most 2 levels. No `else` after `return`.
- A function either computes a value or has side effects (file write, upload, log of a result). Not both, except at the top-level step list.
- Do not mutate an argument in place unless the name says so (`drop_empty_rows`).

### 3. Names

- Functions are verbs: `parse_order`, `count_open_tickets`. Values are nouns.
- Booleans start with `is_`, `has_`, `should_` or `can_`.
- Write words in full. Keep a short list of allowed short forms here: `id`, `url`, ...
- A number with a unit carries the unit in its name: `_m`, `_px`, `_s`, `_bytes`, `_pct`.
- Use the same domain word for the same thing everywhere. Keep a glossary at the end of this file.
- A name that starts with `_` is private to its module. Never import it from another module. Make it public in a shared module instead.

### 4. Types

Annotate every function signature, public and private. Use these formats:

| Kind of data | Use | Do not use |
|---|---|---|
| Config, job input/output, anything serialized to JSON | a pydantic model | `dict`, `TypedDict` |
| Shape of a JSON file written by another service | `TypedDict` | a pydantic model that rejects unknown keys |
| Small internal value with fields | `@dataclass(frozen=True)` | a tuple, a dict |
| Function result with 2 or more values | a frozen dataclass or `NamedTuple` | `tuple[x, y, z]` |
| A fixed set of modes or names | `Literal["a", "b"]` or `StrEnum` | a bare `str` compared by value |
| Optional value | `X \| None` | `Optional[X]` |
| File system path | `pathlib.Path` | `str` |
| Map from name to value | `dict[str, X]` with a precise `X` | `dict`, `dict[str, Any]` |

- `Any` is allowed only at the edge where untyped external data comes in. Convert it to a typed object at once.
- Do not compare config names or mode strings to choose behaviour (`if config_name == "fast"`). Read a typed field instead (`options.is_fast_mode`).

### 5. Constants and magic numbers

- Every threshold, limit and size is a module-level `UPPER_CASE` constant with a unit in its name. Its docstring tells why it has that value.
- One concept has one owner. Other modules import the constant. They do not copy the value.

### 6. Comments and docstrings

- A docstring is one summary sentence. Add more lines only for units, shapes, or a non-obvious rule.
- A comment tells why, never what the next line does.
- No history notes ("previously", "before PR #123"). Git keeps the history.
- No commented-out code.

### 7. Errors and logs

- Fail loudly. Never use a bare `except`. Catch a specific error, or catch broadly only at a stage boundary, and log why the run can go on.
- A log line is one sentence with numbers and units: `log.info(f"Kept {kept} of {total} rows after the {max_age_s} s filter")`.

### 8. One home per concept

- Parsing, unit conversion and shared math each live in one module. Search before you write a helper. Reuse it or move it to the shared home.
- Two variants of a flow run through the same functions. The difference is a typed parameter, not a copied code path.

### 9. Limits that tools enforce

- `tools/loc_check.py`: file 300, class 200, function 60 lines of code (`[tool.loc_check]` in `pyproject.toml`).
- Items that were over a limit when the gate started are listed in `tools/loc_baseline.json` (`--init-baseline`). A listed item may only get shorter. Run `--update-baseline` when it shrinks. Never add an item to the list.
- Do not split a class into mixins, or a module into files, only to pass a limit. Split by responsibility, so each part makes sense on its own.

## Writing style: ASD-STE100

Write every doc and code comment in ASD-STE100 Simplified Technical English.

- Write one fact per sentence. Split any sentence joined by a comma, dash, or semicolon into two sentences.
- Use active voice. Write "Run the script", not "The script should be run".
- Use simple, common words. Avoid jargon where a plain word works.
- Do not stack nouns into clusters. Write "the configuration file for the import job", not "the import job configuration file".
- Avoid gerunds as nouns where a plain verb phrase works. Write "to train the model", not "for training the model".
- Write procedures as numbered steps, one action per step.
- Keep code comments and docstrings terse. Do not turn a dense 2-4 line technical comment into a stack of one-clause-per-line sentences. Merge short, related facts into one line where that reads more clearly.
- Keep every technical detail exact: commands, flags, paths, code blocks, and numbers must not change when you simplify prose around them.

## Glossary

| Word | Meaning |
|---|---|
| (your domain word) | (one line) |
