---
name: vacation-accrual
description: Business context for the Vacation Accrual journal entry (France CP/RTT and
  global MSVacation). Use when working on vacation accrual review, re-performance, the
  GPAY/Pivot/JE Create/Batch JE workbook chain, the JE type context files, or any check in
  the population, classification, salary, leave, accrual, JE-reconciliation or period areas.
---

# Vacation accrual journal entry

Business context for the JEVE review harness. Merged from
`skills/docs/Business Understanding-Vacation accrual.pdf` (the France workbook, tab by tab)
and `skills/docs/Vacation_Accrual_JE_Business_Context.pdf` (the validation framework).

> **Configuration values are not in this file.** Account numbers, the salary-per-day
> denominator, tolerances, area weights and the check list live in
> `app/review/context/je_types/vacation_accrual_france.yaml`, which is SHA-hashed into every
> review's audit trail. Prose that repeats a config value becomes the stale copy. Named
> roles here; values there.

---

## 1. What a vacation accrual is

Employees earn paid vacation as they work. Where they have eligible unused balances at
period end, the organisation recognises the obligation **before** the leave is taken,
because the cost has already been incurred.

> Employees have earned paid leave but have not yet used it. The company records the
> expected cost now because it has already become an obligation.

The entry recognises two things at once: an employee-related **cost already earned**, and an
amount the company **expects to settle in future**. There is no cash movement when it posts.

The entry reverses in the following period, so each month's accrual is recalculated from
current leave and payroll data rather than adjusted incrementally.

## 2. The validation chain

Everything the harness checks hangs off this chain. Each arrow is a place a review can find
an exception.

```
Employee → Employment status → FT/Non-FT classification → Leave balance → Salary
→ Salary per day → Eligible leave → Vacation accrual → Aggregation → JE posting
```

## 3. Leave components

| Component | Meaning | Inputs |
|---|---|---|
| **CP** (congés payés) | Legally required annual paid vacation in France | Days earned, days taken, remaining balance, salary-based value, employer contributions |
| **RTT** (réduction du temps de travail) | Paid time off given in return for additional working time | Days earned, days taken, remaining balance, daily salary rate |

`CP provision + RTT provision = total employee accrual`, then classified as FTE or Non-FTE.

Two calculation models exist across the sample workbooks:

- **CP + RTT** (France, GPAY-style tab) — the two legs above, computed independently. A row
  missing its daily rate still accrues its CP leg; gating the whole row on every input
  understates the total by millions.
- **MSVacation** (global, `DENT`-style tab, 39 currencies on one sheet) — the extract states
  the outstanding day count directly: `total = adjusted_final_days × daily_salary`. No
  CP/RTT split and no proration.

Two behaviours the workbooks actually exhibit, both reproduced deliberately rather than
corrected:

- **The balance is floored at zero.** In one workbook 965 rows had taken more leave than
  earned to date. Without the floor they would *reduce* the provision.
- **A twelfth of RTT is added to a whole-year CP provision** (`total = cp + rtt / 12`). This
  is the workbook's own arithmetic. It is reproduced because it is what was posted, and
  reported per affected row as `rtt_twelfth_applied` rather than silently accepted or
  silently fixed.

## 4. Employee population

| Group | Examples |
|---|---|
| **FTE** | Regular employees, expat / commission-eligible, retail, direct production |
| **Non-FTE** | Co-op employees, interns, visiting researchers |
| **Excluded** | Agency temporary workers, vendors, contractors, fixed-term (per workbook methodology) |

Classification matters financially: FTE and Non-FTE post to **different expense accounts**.
A misclassified employee produces a correct total in the wrong account.

## 5. The workbook, tab by tab

The France workbook has ten tabs. Only the first six carry calculation or validation.

| Tab | Role | What it does |
|---|---|---|
| **Methodology** | Reference | States the business rules: vacation year and periods, posting and reversal dates, employee categories, inclusions and exclusions, FTE/Non-FTE rules, CP and RTT definitions, account mapping, validation requirements |
| **Check Pivots** | Self-check | Summarises Batch JE by header, currency, company code, account, classification, current vs prior period, change and change %, headcount, vacation hours |
| **Batch JE** | Output | The final SAP upload format: header ID, reference, fiscal year and period, currency, document type, company code, posting date, reversal date, account, cost centre, amount, description |
| **JE Create** | Bridge | Turns summarised amounts into JE lines by company code and cost centre, assigns the expense account, drops zero-value lines |
| **Pivot Tab** | Aggregation | Groups employee-level amounts by company code, currency and cost centre into FTE and Non-FTE totals |
| **GPAY** | Calculation | The employee-level tab. Per employee: company code, reference, CP days earned/taken/balance, CP provision, employer contribution rate and amount, RTT days earned/taken/balance, RTT provision, daily salary rate, currency, start date, months eligible, staffing type, FTE/Non-FTE, FTE and Non-FTE accrual, total, cost centre |
| **Data Table** | Master data | Valid accounting setup per France entity: company codes and names, country, posting currency, calculation method, balance-sheet account, FTE and Non-FTE expense accounts |
| **MSPeople** | Master data | Employee reference, SAP company, cost centre, staffing type, SAP company start date, active indicator |
| **Settings** | Config | Identifies selected columns in MSPeople, prior-period data and GPAY |
| **Original File Link** | Provenance | Location of the source workbook |

**Tab names are not reliable identifiers.** One sample workbook binds its entire calculation
on a tab named `DENT`. Identify tabs by the columns they contain — see the
`je-type-identification` skill.

## 6. How the tabs connect

```
Methodology + Data Table (reference)
        ↓
GPAY — employee-level payroll and leave
        ↓   validated against MSPeople and Data Table
Pivot Tab — grouped by company code and cost centre
        ↓
JE Create — journal entry lines, zero-value lines dropped
        ↓
Batch JE — SAP upload format
        ↓
Check Pivots — the workbook's own balance and reasonableness check
```

Four roll-up hops, each a place an amount can be lost. **Validate them hop by hop**: a
matching grand total proves nothing when two errors cancel.

## 7. Account logic and reconciliation

Three accounts, named by role (values in the YAML):

| Role | Side | Applies to |
|---|---|---|
| FTE vacation expense | Debit | FTE employees |
| Non-FTE vacation expense | Debit | Non-FTE employees |
| Vacation accrual liability | Credit | All employees |

```
FTE expense + Non-FTE expense = Vacation accrual liability
Total debits + total credits    = 0
```

Illustrative, from workbook `305800.xlsx`: FTE 27,839,758.12 + Non-FTE 66,054.52 =
liability 27,905,812.64, net 0.00. A small decimal difference from spreadsheet rounding is
expected and must be judged against the configured tolerance, not against exact zero.

**Financial impact:** vacation expense increases (reducing profit for the period), the
liability increases by the same amount, and there is no cash movement.

## 8. Reversal

The entry reverses in the following period, reducing both the expense and the liability so a
fresh accrual can be recorded. The automation must confirm:

- the reversal date is **later** than the posting date;
- the reversal uses the **same accounts with opposite signs**;
- a reversed entry is replaced by the updated accrual for the next period;
- the entry is **not duplicated** across periods.

## 9. Key business risks

| Risk | Concern |
|---|---|
| Incorrect population | Employees missing, duplicated, ineligible, inactive or wrongly included |
| Incorrect salary | Outdated, mapped to the wrong employee, outside the period, or wrong compensation basis |
| Incorrect leave balance | Opening balance, earned, taken or adjustments incomplete or inaccurate |
| Incorrect methodology | Wrong denominator, eligible leave, carry-forward, negative balances, joiners/leavers, salary components |
| Classification error | Wrong FT/Non-FT assignment → wrong calculation and wrong account |
| Period / cut-off | Leave, salary or employee data from the wrong period |
| Unsupported manual adjustment | Overrides or hard-coded values without support |
| JE-to-working mismatch | Aggregation, sign, posting or manual-entry error between working and JE |

## 10. Required evidence

| Evidence | Expected content |
|---|---|
| Employee master | ID, name, status, employment type, FT/Non-FT, join/termination dates, eligibility |
| Salary master | ID, monthly salary, currency, effective date, period, compensation type, status |
| Leave / vacation data | ID, opening balance, earned, taken, adjustments, closing balance, leave type, as-of date |
| Vacation accrual working | Employee-level calculation, salary, salary per day, balances, eligible leave, classification, adjustments, totals |
| Methodology / policy | Eligibility, eligible leave types, salary basis, daily-rate method and denominator, FT/Non-FT treatment, joiner/leaver, carry-forward, adjustments, rounding, cut-off |
| Journal entry | JE number, dates, period, account, debit/credit, amount, entity, currency, description, preparer, approver |

On the sample workbooks, **Approval** and **Vacation provision file** came back *Missing* on
every file tested. Expect those to fail on real submissions until preparers attach them —
that is a finding, not a defect in the harness.

## 11. The eight validation layers

| Layer | Validates |
|---|---|
| 1 Population | Working vs employee master: missing, unexpected, duplicate, inactive |
| 2 Classification | FT/Non-FT in the working vs the authoritative employee source |
| 3 Salary | Salary used vs salary source; effective date; employee mapping; salary per day |
| 4 Leave recalculation | `Opening + earned − taken ± adjustments = closing`, independently |
| 5 Accrual re-performance | `Eligible leave × salary per day`, per employee, compared to the working |
| 6 FT/Non-FT aggregation | `FT accrual + Non-FT accrual = total expected accrual` |
| 7 JE reconciliation | Expected total → working → posted JE, hop by hop |
| 8 Period / cut-off | Reporting period, salary effective date, leave as-of date, status, posting period |

## 12. What not to build

This is the load-bearing instruction from the business context, kept verbatim:

> **Do not limit the automation to: JE Amount = Working Amount → PASS.** Such a check
> validates only the final reconciliation and assumes that the working is correct. The
> intended AI reviewer should independently validate the business logic behind the working.

> Do not validate only whether the JE agrees with the working. Independently establish
> whether the underlying business event, source population, inputs, methodology, calculation
> and final accounting entry are complete, accurate, appropriate and supported by evidence.

Three further rules that follow from it:

- **The salary-per-day denominator comes from the approved methodology.** Never assume 30
  days unless that *is* the established policy.
- **Check Pivots is the preparer's own homework.** Re-derive those pivots from Batch JE
  independently; a passing self-check is a claim to verify, not evidence.
- **Negative employee-level accruals are never auto-approved.** They are flagged for review
  to confirm whether the value is expected or caused by bad source data, leave information
  or employee mapping.

## 13. Where things live

| What | Where |
|---|---|
| Config: accounts, denominator, tolerances, areas, checks | `app/review/context/je_types/vacation_accrual_france.yaml` |
| How a workbook is recognised as this JE type | `je-type-identification` skill |
| The re-performance engine and its gate | `skills/docs/Accrual Re-performance Architecture.md` |
| The arithmetic as the workbooks actually compute it | `skills/docs/accrual-model.md` |
| Architecture analysis and the merge plan | `skills/project_geeta.md` §7–§10 |
| Source PDFs and their markdown conversions | `skills/docs/` |
