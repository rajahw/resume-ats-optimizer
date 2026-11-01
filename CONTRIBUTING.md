# Contributing

Issues and pull requests are welcome. By submitting a contribution, you agree that it is
licensed under the same [PolyForm Noncommercial License 1.0.0](LICENSE.md) as the rest of
the project, and that the project's copyright holder may also license your contribution
to others, including commercially, as part of this project.

Before opening a pull request, run the tests:

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest
```

GitHub Actions runs the same suite on every pull request, on Linux, macOS, and Windows.

Never include a real resume, a filled `pii.txt`, or a job posting's text in an issue,
pull request, or test fixture. Every fixture in `tests/` is synthetic.
