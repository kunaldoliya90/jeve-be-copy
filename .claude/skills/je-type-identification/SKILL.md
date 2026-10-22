---
name: je-type-identification
description: How the JEVE harness decides what JE type an uploaded workbook is, and how to
  add a new type's discriminators. Use when working on JE type classification, the layout
  fingerprint or learned-mapping registry, AI-1 Understand, the sheet map, the
  entry_is_<type> check, strategy selection, or when adding a JE type to the catalogue.
---

# Identifying a JE type from a workbook

Identification decides which checks run, which arithmetic is re-performed and which accounts
are expected. Get it wrong and every downstream result is confidently wrong — so the design
is biased toward **refusing to guess**.

> A new variant binding to the wrong arithmetic produces *a number* — strictly worse than an
> unsupported workbook, because nothing looks wrong.

---

## 1. Three tiers, cheapest first

| Tier | Mechanism | Cost | Lives in |
|---|---|---|---|
| 1 | **Layout fingerprint** → learned registry | Zero AI, exact, replayable | `review/context/caches.py` |
| 2 | **Shape discriminators** — required column families + `rejects` | Zero AI, deterministic | `je_types/*.yaml` → `discriminators:` |
| 3 | **AI-1 blind classification** against the catalogue | ~9.5k tokens | `review/agents/understand.py` |

Each tier runs only when the one above it does not settle the question. In steady state most
files are decided at tier 1 and cost nothing.

### Tier 1 — the fingerprint

Taken over the **sorted, normalised header set**, so reordering a column does not bust the
cache:

```python
def fingerprint(header_row: list[object]) -> str:
    labels = sorted(normalize_header(cell) for cell in header_row if not is_blank(cell))
    return sha256("|".join(labels).encode()).hexdigest()
```

Row counts, values and dates are excluded, so next month's file from the same template
produces the same fingerprint. Once a mapping clears the gate it is **frozen** into the
registry with its fingerprint, what produced it and the gate result that cleared it — which
is what lets a reviewer reopen the work paper in March and get the same figure to the cent.

Two layers, read in order:

| Layer | Holds | Trust |
|---|---|---|
| `mappings.json` | Blessed mappings, checked in, human approves the diff | Default |
| `template_mapping` | Learned at runtime by the reviewer screen or the proposer | On record |

Nothing is promoted from learned to blessed automatically; a pull request does it.

### Tier 2 — shape discriminators

Deterministic rules over the column families a sheet contains. This is where a JE type's
identity actually lives.

```yaml
discriminators:
  precedence: 10                      # lower numbers evaluated first
  variants:
    - id: cp_rtt_france
      requires_all: [cp_provision, rtt_provision, daily_salary_rate]
      rejects: [adjusted_final_days]
    - id: ms_vacation_global
      requires_all: [adjusted_final_days, daily_salary]
      rejects: [cp_provision, rtt_provision]
  corroborating:                      # must cite >=1 cell outside the posting tab
    - accounts_present: [liability, fte_expense, non_fte_expense]
    - fte_classification_column: true
    - reversal_date_populated: true
  never:
    - tab_name_match
```

Three parts, and all three are load-bearing:

- **`requires_all`** — the column families that must be present. Families, not literal
  headers, so aliasing and translation are handled once in the alias table.
- **`rejects`** — what disqualifies the variant. Without this a near-miss binds to the wrong
  arithmetic and returns a plausible number.
- **`precedence`** — ordering matters. CP+RTT is tested before MSVacation because a GPAY
  sheet *also* carries a daily-salary column and would match the looser variant.

### Tier 3 — AI-1, blind

Only on a tier-2 miss or ambiguity. AI-1 receives the JE type **catalogue** (id, name,
aliases, objective per type) plus the `StructureContext` — and **never the submitted JE
type**. It returns `je_type`, `je_type_confidence` and `je_type_citations`.

The blindness is the whole point: the `entry_is_<type>` check compares AI-1's independent
answer with what the preparer selected. Tell it the answer and the check measures nothing.

---

## 2. Four rules

### Rule 1 — never identify by tab name

One sample workbook binds its entire calculation on a tab named `DENT` — 37,613 rows, where
no name-based rule would ever look. **A tab is identified by the columns it contains.**
`never: [tab_name_match]` is in the schema to make this refusable rather than merely
discouraged.

### Rule 2 — the catalogue must stay symmetric

Every JE type gets the **same depth** of description in the catalogue: id, name, aliases,
one-line objective. Nothing more.

Give one type a rich fingerprint while others keep a single line and the classifier will
over-match the rich one — you would be measuring description length, not evidence. Detail
belongs in tier-2 discriminators, which are evaluated deterministically and symmetrically,
never in catalogue prose.

This is also why **a per-JE-type identification document is the wrong artifact**.
Identification is comparative: you decide what a workbook *is* by discriminating against
what else it could be. A single-type document cannot express "not this, because it looks
more like that".

### Rule 3 — corroborate outside the posting tab

`je_type_citations` must include at least one cell **outside** the posting table (a
map-check rule, medium severity). Batch JE's own labels are the preparer's assertion; the
calculation tab, methodology and master data are evidence. Put the corroborating signals in
`corroborating:` so this is checkable rather than hoped for.

### Rule 4 — unknown is a good answer

No variant matches → `unknown` → `unable_to_validate` → needs_review, with
`unresolved_headers` listed so the fix is a two-minute alias addition rather than an
investigation.

| Outcome | Result |
|---|---|
| Tier 2 matches one variant | Proceed, zero AI |
| Tier 2 matches none | AI-1 at tier 3 |
| Tier 2 matches **more than one** | Precedence decides; if still tied → `unknown` |
| AI-1 returns `unknown` | `entry_is_<type>` fails if a type was submitted, else `unable_to_validate` |

An unsupported workbook is a good result. A confidently misclassified one is the failure
mode this whole design exists to prevent.

---

## 3. Adding a new JE type

1. **Add the catalogue entry** — id, name, aliases, one-line objective. Nothing more
   (Rule 2).
2. **Write the discriminators** — column families in `requires_all`, disqualifiers in
   `rejects`, a `precedence` relative to existing types. Check the rejects against every
   *existing* type's sample files, not only the new one.
3. **Declare the rejects on the existing types too.** A new variant makes previously
   unambiguous types ambiguous. This is a two-way edit.
4. **Add alias entries** for the new column families so headers resolve across spellings,
   translations and abbreviations.
5. **Add mutation cases** — shuffle columns, substitute header synonyms, translate, push the
   header row down, strip the hash total. Every case has a known-correct answer because the
   mutation is known.
6. **Confirm the eval gate still passes** — in particular that no existing type's files now
   match the new variant.

## 4. What identification is not responsible for

- **Binding columns to meanings** — tier 1 and 2 identify the *type*; the sheet map binds
  the *columns*. A correct type with a wrong binding still fails the gate.
- **Deciding the answer** — identification selects which checks run. The figure comes from
  re-performance, and the gate decides whether it may support a pass.
- **The comparison column** — `POSTED_TOTAL` and the sheet's own subtotal are bound by
  deterministic alias resolution only. A proposed mapping that names either, or binds an
  input to the same physical column as the comparison, is `invalid_mapping` and is not
  scored. Otherwise the verifier marks the proposal against a column the proposal chose, and
  0.00 stops meaning anything.

## 5. Where things live

| What | Where |
|---|---|
| Discriminators per JE type | `app/review/context/je_types/<type>.yaml` → `discriminators:` |
| Blessed mappings | `mappings.json` (human approves the diff) |
| Learned mappings | `template_mapping` registry, written at runtime |
| Fingerprint and caches | `app/review/context/caches.py` |
| AI-1 and its schema | `app/review/agents/understand.py`, `agents/schemas/` |
| Map check (verifies AI-1) | `app/review/guardrails/map_check.py` |
| The `DENT` case and strategy rejects | `skills/docs/Accrual Re-performance Architecture.md` §2, §5 |
| Vacation accrual business context | `vacation-accrual` skill |
