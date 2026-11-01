# Sources

Every external fact, dependency, or behavior this project relies on, with where it was
checked. Verified 2026-10-31. "Primary" means the project's own documentation, registry,
or specification. "Secondary" means press, blogs, or third-party mirrors. "Tested" means
checked directly on a machine, and "Observed" means behavior seen in practice that no
official documentation describes. Observed behavior can change without notice.

## Claude Code

| Claim | Status | Source |
|---|---|---|
| A `PreToolUse` hook can deny a call by printing `hookSpecificOutput.permissionDecision: "deny"` with a reason | Primary | [Hooks reference](https://code.claude.com/docs/en/hooks) |
| Exit code 2 blocks the call; any other failure (other exit code, not starting, invalid JSON) is non-blocking and the call proceeds | Primary | [Hooks reference](https://code.claude.com/docs/en/hooks), "Exit codes" |
| `"onFailure": "block"` makes a failing hook block the call; requires Claude Code 2.1.295+ | Primary | [Hooks reference](https://code.claude.com/docs/en/hooks), "Block the action when a hook fails" |
| Matcher `"*"` fires on every tool call; `timeout` and `statusMessage` are valid hook fields; `$CLAUDE_PROJECT_DIR` is the project root | Primary | [Hooks reference](https://code.claude.com/docs/en/hooks) |
| Shell-form hooks run in Git Bash on Windows, or PowerShell when Git Bash isn't installed; `CLAUDE_CODE_GIT_BASH_PATH` points at Git Bash | Primary | [Hooks reference](https://code.claude.com/docs/en/hooks), [Setup](https://code.claude.com/docs/en/setup) |
| Git for Windows is optional for Claude Code itself (needed by its Bash tool) | Primary | [Setup](https://code.claude.com/docs/en/setup), "Set up on Windows" |
| Interactive sessions hold back settings-file hooks until the folder is trusted; `-p`/SDK sessions treat it as trusted | Primary | [Hooks reference](https://code.claude.com/docs/en/hooks), "Workspace trust" |
| Project `permissions.allow` rules apply only after workspace trust; `deny` rules apply immediately | Primary | [Permissions](https://code.claude.com/docs/en/permissions), "Project allow rules and workspace trust" |
| `Bash(cmd *)` rule syntax: the space before a trailing `*` is part of the rule; `Read` rules use gitignore patterns | Primary | [Permissions](https://code.claude.com/docs/en/permissions) |
| `.claude/` is a protected path: writes prompt in default mode and go to the classifier in auto mode | Primary | [Permission modes](https://code.claude.com/docs/en/permission-modes), "Protected paths" |
| Project skills live in `.claude/skills/<name>/SKILL.md` and load for sessions in the repository | Primary | [Skills](https://code.claude.com/docs/en/skills) |
| Skill `model:` applies only to the invoking turn; the session model resumes on the next prompt | Primary | [Skills](https://code.claude.com/docs/en/skills), frontmatter reference |
| Skill `allowed-tools:` grants permission only during the invoking turn; the grant clears on the next message | Primary | [Skills](https://code.claude.com/docs/en/skills), frontmatter reference |
| `.claude/settings.json` is meant to be committed and shared | Primary | [Settings](https://code.claude.com/docs/en/settings) |
| Claude Code requires a Pro, Max, Ultra, Team, Enterprise, or Console account; the free plan doesn't include it | Primary | [Setup](https://code.claude.com/docs/en/setup), "Authenticate" |

## LibreOffice

| Claim | Status | Source |
|---|---|---|
| `--headless`, `--convert-to <ext> --outdir <dir>`, and `-env:UserInstallation=file:///...` are supported command-line options | Primary | [LibreOffice start parameters](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html) |
| A running LibreOffice instance intercepts a headless conversion, which then exits without converting; a separate user profile avoids it | Secondary | [Bugzilla 37531](https://www.libreoffice.org/bugzilla/show_bug.cgi?id=37531), [Bugzilla 40807](https://www.libreoffice.org/bugzilla/show_bug.cgi?id=40807), [Ask LibreOffice](https://ask.libreoffice.org/t/how-to-not-connect-to-a-running-instance/534) |
| Flatpak app ID is `org.libreoffice.LibreOffice`, installed under `/app/libreoffice` | Primary | [Flathub](https://flathub.org/apps/org.libreoffice.LibreOffice), [Flathub manifest](https://github.com/flathub/org.libreoffice.LibreOffice) |
| `soffice` is at `/app/libreoffice/program/soffice` inside the Flatpak, the sandbox's `/tmp` is private, and a conversion with a profile under `~/.cache` works | Tested | Flatpak LibreOffice 26.8.2.2 on Fedora |
| Flatpak doesn't share the host's `/tmp` with the sandbox | Primary | [Flatpak command reference](https://docs.flatpak.org/en/latest/flatpak-command-reference.html), `host` filesystem keyword |
| Homebrew cask `libreoffice` installs `/Applications/LibreOffice.app` | Primary | [Homebrew cask API](https://formulae.brew.sh/api/cask/libreoffice.json) |
| Package names: `libreoffice-writer` on Debian/Ubuntu and Fedora | Primary | [Debian](https://packages.debian.org/stable/libreoffice-writer), [Fedora](https://packages.fedoraproject.org/pkgs/libreoffice/libreoffice-writer/) |
| winget ID `TheDocumentFoundation.LibreOffice` | Primary | [winget-pkgs manifests](https://github.com/microsoft/winget-pkgs/tree/master/manifests/t/TheDocumentFoundation/LibreOffice) |
| On Windows, `soffice.exe` is at `{install}\program\`, e.g. `C:\Program Files\LibreOffice` | Primary | [LibreOffice start parameters](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html) |
| On macOS, the binary is `/Applications/LibreOffice.app/Contents/MacOS/soffice` and isn't on `PATH` | Secondary | [Ask LibreOffice](https://ask.libreoffice.org/t/using-libreoffice-with-terminal/45653), [users list, 2016](https://listarchives.libreoffice.org/global/users/2016/msg02918.html) |
| A missing font is replaced by a fallback with different metrics | Tested | `fc-match Garamond` returns Noto Serif on this machine; [fc-match(1)](https://manpages.debian.org/bookworm/fontconfig/fc-match.1.en.html) documents `--format` |
| LibreOffice writes link targets as URLs (`[Token]` becomes `%5BToken%5D`, with a `../` prefix when there is no scheme) | Observed | Seen in documents LibreOffice saved; handled by `normalize_hrefs` |

## Poppler

| Claim | Status | Source |
|---|---|---|
| `pdftotext -bbox-layout` gives box coordinates per block, line, and word; `-layout` keeps the physical layout | Primary | [pdftotext(1)](https://manpages.debian.org/bookworm/poppler-utils/pdftotext.1.en.html) |
| `pdffonts` lists the fonts used in a PDF | Primary | [pdffonts(1)](https://manpages.debian.org/bookworm/poppler-utils/pdffonts.1.en.html) |
| Embedded subset fonts are named with a six-letter tag plus `+` (e.g. `ABCDEF+Garamond`) | Secondary | PDF reference §5.5.3 as summarized by [DevExpress](https://docs.devexpress.com/OfficeFileAPI/DevExpress.Pdf.PdfFont.SubsetName) |
| Packages: Homebrew `poppler`, Debian/Ubuntu/Fedora `poppler-utils`, winget `oschwartz10612.Poppler` | Primary | [Homebrew](https://formulae.brew.sh/api/formula/poppler.json), [Debian](https://packages.debian.org/stable/poppler-utils), [Ubuntu](https://packages.ubuntu.com/noble/poppler-utils), [Fedora](https://packages.fedoraproject.org/pkgs/poppler/poppler-utils/), [winget-pkgs](https://github.com/microsoft/winget-pkgs/tree/master/manifests/o/oschwartz10612/Poppler) |
| The winget Poppler install may not add itself to `PATH` | Secondary | [Bug report](https://claudeissues.com/issue/75421-read-tool-reports-pdftoppm-is-not-installed-on-windows-despite-poppler-being-on) |

## Python, git, and the platforms

| Claim | Status | Source |
|---|---|---|
| Python 3.7+ is required: `subprocess.run(capture_output=, text=)` arrived in 3.7; everything else is standard library | Primary | [subprocess docs](https://docs.python.org/3/library/subprocess.html); the scripts' syntax parses as 3.6 |
| winget IDs `Python.Python.3.12` and `Git.Git` | Primary | [winget-pkgs: Python](https://github.com/microsoft/winget-pkgs/tree/master/manifests/p/Python/Python/3/12), [winget-pkgs: Git](https://github.com/microsoft/winget-pkgs/tree/master/manifests/g/Git/Git) |
| On Windows, `python`/`python3` can be App Execution Alias stubs that open the Microsoft Store instead of running | Secondary | [bpo-41327](https://bugs.python.org/issue41327), [bpo-40687](https://www.python.org/pipermail/new-bugs-announce/2020-May/039479.html) |
| Creating symlinks on Windows without admin rights needs Developer Mode | Primary | [Windows Developer blog, 2016](https://blogs.windows.com/windowsdeveloper/2016/12/02/symlinks-windows-10/) |
| `core.hooksPath` may be absolute or relative; relative paths resolve from where hooks run | Primary | [git-config](https://git-scm.com/docs/git-config), [githooks](https://git-scm.com/docs/githooks) |
| Client-side hooks are not copied when you clone | Primary | [Pro Git, "Git Hooks"](https://git-scm.com/book/en/v2/Customizing-Git-Git-Hooks) |
| `eol=lf` in `.gitattributes` keeps LF on checkout; a CRLF shebang makes the kernel look for an interpreter named `python3\r` | Primary / Secondary | [gitattributes](https://git-scm.com/docs/gitattributes); [explainer](https://iotools.cloud/?p=235796) |
| Homebrew installs with the script on brew.sh | Primary | [brew.sh](https://brew.sh/) |
| Google Docs exports Word via File → Download → Microsoft Word (.docx) | Secondary | [Digital Citizen](https://www.digitalcitizen.life/how-to-download-a-google-doc-as-a-word-document/), [UW-Stout KB](https://kb.uwstout.edu/101534); Google's help page needs JavaScript and couldn't be quoted |
| Aptos (Microsoft 365's default font since 2023) and Calibri are proprietary Microsoft fonts | Secondary | [TechSpot](https://www.techspot.com/news/99410-goodbye-calibri-hello-aptos-new-default-font-microsoft.html) |

## Tests and CI

| Claim | Status | Source |
|---|---|---|
| pytest 9.2.0 requires Python 3.10+ | Primary | [PyPI: pytest 9.2.0](https://pypi.org/project/pytest/9.2.0/), `requires_python` |
| GitHub Actions only runs workflows from `.github/workflows/` | Primary | [Workflow syntax](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions) |
| Dependabot version updates need a `.github/dependabot.yml`; `pip` and `github-actions` are supported ecosystems | Primary | [Dependabot options reference](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference) |
| Current majors: `actions/checkout` v7, `actions/setup-python` v7; Python 3.15 is available on hosted runners | Primary | [checkout releases](https://github.com/actions/checkout/releases), [setup-python releases](https://github.com/actions/setup-python/releases), [python-versions manifest](https://github.com/actions/python-versions/blob/main/versions-manifest.json) |
| CI LibreOffice packages: Ubuntu `libreoffice-writer-nogui` (24.04, 26.04), Chocolatey `libreoffice-fresh` | Primary | [Ubuntu packages](https://packages.ubuntu.com/search?keywords=libreoffice-writer-nogui&searchon=names&exact=1), [Chocolatey](https://community.chocolatey.org/packages/libreoffice-fresh) |

## Documents and job postings

| Claim | Status | Source |
|---|---|---|
| An ODF package's `mimetype` file must be the first zip entry, uncompressed, with no extra field | Primary | [OpenDocument 1.3 Part 2, §3.3](https://docs.oasis-open.org/office/OpenDocument/v1.3/os/part2-packages/OpenDocument-v1.3-os-part2-packages.html) |
| Job boards publish postings as schema.org `JobPosting` JSON-LD (`title`, `hiringOrganization`, `description`) | Primary | [schema.org/JobPosting](https://schema.org/JobPosting), [Google Search: job posting structured data](https://developers.google.com/search/docs/appearance/structured-data/job-posting) |
| LinkedIn's logged-out page for a job is `/jobs/view/<id>/`; its markup classes, and Greenhouse's, are what `extract_posting` reads | Observed | Undocumented markup; may change at any time |
| Some boards (Lever) double-escape line breaks in JSON-LD | Observed | Handled in `extract_posting` |

## Hiring and ATS claims

| Claim | Status | Source |
|---|---|---|
| "75% of resumes are rejected by ATS" has no published methodology and is commonly traced to 2012 Preptel marketing | Secondary | [Enhancv](https://enhancv.com/blog/does-ats-reject-resumes/), [HiringThing](https://blog.hiringthing.com/applicant-tracking-system-myths); the original Preptel material wasn't found |
| In a 2025 survey of 25 U.S. recruiters, 92% said their ATS doesn't auto-reject; 8% used content thresholds; all used yes/no knockout questions | Secondary (small sample) | [BetaNews on the Enhancv study](https://betanews.com/2025/11/10/study-debunks-viral-claim-that-ai-filters-out-most-job-applicants/), [HCAMag](https://www.hcamag.com/us/specialization/hr-technology/whats-the-real-cause-of-candidate-rejections/556067) |
| Tables, text boxes, graphics, and header/footer contact details parse unreliably; plain characters are safest | Secondary (mostly career sites) | [Roche resume parsing FAQ](https://careers.roche.com/global/en/resume-parsing-faq), [DBTA](https://www.dbta.com/Columns/Emerging-Technologies/Resumes-Data-Parsing-and-How-Candidates-Get-Noticed-in-Database-Driven-Hiring-176075.aspx) |
| Recruiter search matches exact terms, so the posting's wording beats a synonym | Not verified | A design assumption; no vendor documentation found. The tool claims nothing more than this. |

## License

| Claim | Status | Source |
|---|---|---|
| PolyForm Noncommercial 1.0.0 allows any noncommercial purpose, including changes and redistribution, and listed personal uses "without any anticipated commercial application" | Primary | [LICENSE.md](../LICENSE.md), from [polyformproject.org](https://polyformproject.org/licenses/noncommercial/1.0.0) |
| Using the tool for your own job search counts as permitted use | Interpretation | The license text doesn't name job seeking. The README's License section states it as the licensor's intent. |
