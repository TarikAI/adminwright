# Evals

Golden fixtures that pin the validator's behaviour.

The learning loop changes guidance and rules over time. Without a fitness function that is
drift, not improvement — a rule tightened for one project starts failing builds that were
always correct, and nobody notices until a user does.

These fixtures judge the validator. What an agent does with the skill is tested separately,
by the `claude plugin eval` suite in [behavior/](behavior/README.md).

## The fixtures

| Fixture | Shape | Job |
|---|---|---|
| `saas-clean` | Multi-tenant B2B SaaS: tenants, subscriptions, a Stripe integration | Passes at every profile. Proves honest work is never blocked |
| `logistics-gaps` | Dispatch platform with named, deliberate defects | Each defect targets one rule. If one stops firing, that guarantee has silently disappeared |
| `gate-matrix` | One console broken on purpose | Every rule the validator advertises fires at least once. `tests/test_rule_coverage.py` fails when a rule is in `RULES` but in no expectation (or, for the few this fixture cannot reach, in no named test) |

`saas-clean` is worth reading as a worked example: it is the smallest manifest that satisfies
the `regulated` profile honestly, including a `gaps[]` entry explaining why no admin command
can move a subscription into `active` — that state is set by a payment webhook, and letting
operators fake it would grant paid access without money moving.

## Running

```bash
python evals/run.py            # assert behaviour matches
python evals/run.py --update   # re-record expectations; read the diff
python evals/build_fixtures.py # regenerate the fixture manifests
```

CI runs `evals/run.py` on every push, across the full OS and Python matrix.

## When a fixture flips

The runner compares the rules that fire per profile, with their severity and how many times
each fires. It does not compare message text, so wording can improve freely. Fixtures pin
their `platform.id` and `validatedWith` stamp, so regenerating them on any commit is
byte-for-byte stable.

A flip means one of two things is true, and you must decide which:

- **The change is wrong.** Scope it to a profile, or drop it.
- **The expectation was wrong.** Update it in the same commit as the change, and say why in
  the commit message.

Never run `--update` to make CI quiet. A silent re-record is how a validator stops validating.

## Adding a fixture

Add a builder to `build_fixtures.py` rather than hand-writing JSON: generated manifests stay
consistent with the template as the schema evolves, and a reviewer can read the *intent*
instead of diffing 200 lines. Comment each deliberate defect with the rule it targets. Then
`build_fixtures.py`, `run.py --update`, and commit both.

Fixtures earn their place by covering a shape the others do not — a different archetype, a
different tenancy model, a defect class nothing else exercises.
