# Groq detection fixtures (phase G2)

Inputs for `tests/scanner/test_detect_groq.py`: parsed with `ast.parse`, never imported or run.
They live outside `tests/fixtures/parse` so that directory's repo-level detection
(`test_fixture_directory_detection`) is unchanged. No fixture app: G4 adds one with runtime
expectations.
