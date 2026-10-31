# Applications live in gitignored per-posting folders, not git history

Tailoring used to write one fixed set of outputs to the repo root, overwritten each run, with "commit between applications" making git history the per-job archive. Batching 10+ postings in a sitting needs every application's outputs to exist at once, so each application now gets its own folder under `applications/` (`YYYY-MM-DD/YYYY-MM-DD_<company>_<role>/`, grouped by batch) holding its posting, edit set, tokenized résumé and filled résumé. The folders are gitignored: posting text is the employer's copyrighted content and the list of employers applied to is private, so git could never have been the full archive anyway, and dropping the commit step removes a per-application chore.

## Consequences

- There is no versioned history of past applications; the folders on disk are the record, so backing them up is the user's job.
- A folder for the same company and role already existing, in any batch, is surfaced during consultation as a possible duplicate application, never overwritten.
- The filled résumé is `<PREFIX>_Resume.pdf` and the tokenized one `<PREFIX>_Resume_tokenized.odt`, so the PII hook still blocks reading filled résumés back.
