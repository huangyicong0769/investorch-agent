# Behavioral contract tests

Tests protect observable behavior, not implementation structure.

A test should survive an internal rewrite that preserves the contract.

- Do not test private methods or fields.
- Do not assert collaborator call counts.
- Use real SQLite and filesystem resources where practical.
- Use controlled fakes only at explicit external or application boundaries.
- If a behavior cannot be tested without implementation coupling, leave it untested.
- Coverage is diagnostic, not a target.
