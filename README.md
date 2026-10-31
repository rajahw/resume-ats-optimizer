# resume-ats-optimizer

Tailor your resume to each job you apply for, in your own words, without making anything up.

You give it your resume and a stack of job postings. For each posting, it finds the skills and terms the employer asks for that your resume doesn't mention, asks you about them once, and makes a few small edits so the resume uses the employer's own wording. Every edit is written to sound like the rest of your resume. You get one PDF per job, ready to submit, the same length as your original.

## What it does and doesn't do

- **Does** swap your wording for the posting's exact terms ("patient education" instead of "taught patients"), add a confirmed skill to your skills section, reorder entries so the most relevant one comes first, and lengthen or trim a bullet to fit.
- **Does** ask before claiming anything. If a posting wants something your resume doesn't mention, you're asked whether you have it. Your answers are remembered, so each question is asked only once.
- **Does** check every result: its text still comes out cleanly and in order when extracted from the PDF (the first step any resume parser takes), it runs no longer than your original, and nothing spills past the bottom margin.
- **Doesn't** invent experience, add sections you don't have, or rewrite your resume in a generic style.
- **Doesn't** score your resume or promise it will "beat the ATS". Nobody outside an employer can measure that, so it never claims to.

## What you need

- A Claude plan that includes Claude Code: Pro, Max, Ultra, Team, Enterprise, or a Console (API) account. The free plan doesn't include it. The skill runs on the Claude Sonnet model.
- Claude Code version 2.1.295 or newer (`claude --version` shows yours). Older versions can't make the privacy guard block when it fails to start.
- A computer running Windows, macOS, or Linux. On Windows you also need **Git for Windows**: Claude Code itself works without it, but the privacy guard is a shell script that runs in the Git Bash it provides.
- Your resume as a Word file (`.docx`), a Google Doc (download it as Word), or a LibreOffice file (`.odt`). It needs a simple layout: one column, section headings, and each job's dates on its title line (after a tab or a `|`). Descriptions can be bullets or short paragraphs. Content inside tables, text boxes, or two-column templates may not be editable; the tool's first check tells you what it found.
- Three free programs: **Python 3**, **LibreOffice**, and **Poppler**. They convert and measure the resume.

## Set up (once)

1. **Install Claude Code.** Follow Anthropic's [setup guide](https://code.claude.com/docs/en/setup). On Windows, also install [Git for Windows](https://git-scm.com/downloads/win) (`winget install --id Git.Git --exact`).

2. **Install the three programs.** Open a terminal (on Windows, use *Git Bash*) and run the lines for your system:
   | System | Commands |
   |---|---|
   | macOS (needs [Homebrew](https://brew.sh)) | `brew install python poppler` then `brew install --cask libreoffice` |
   | Windows | `winget install --id Python.Python.3.12 --exact` then `winget install --id TheDocumentFoundation.LibreOffice --exact` then `winget install --id oschwartz10612.Poppler --exact` |
   | Ubuntu / Debian | `sudo apt install python3 libreoffice-writer poppler-utils` |
   | Fedora | `sudo dnf install python3 libreoffice-writer poppler-utils` |

    On Windows, close and reopen the terminal afterwards so it finds the new programs.

3. **Download this project.** On this page, click **Code → Download ZIP** and unzip it somewhere you'll find it, such as your Documents folder. (If you use git, cloning works too.)

4. **Check it.** In the terminal, go to the unzipped folder and run the setup check:

   ```bash
   cd path/to/resume-ats-optimizer
   bash install.sh
   ```

   It says what it found and how to install anything still missing.

5. **Fonts.** The computer needs every font your resume uses, or page measurements come out wrong. The check names any that are missing. Fonts that come with Microsoft Office (Calibri, Aptos, Garamond) are the usual ones missing on Linux.

## Use it

1. In the terminal, from the project folder, start Claude Code:

   ```bash
   claude
   ```

   The first time, it asks you to trust the folder and its *hooks*. Say yes. The hooks
   keep your personal details private (see below).

2. Type `/model sonnet`, then `/resume-ats-optimizer`. The skill is built and priced for Sonnet; it switches to Sonnet for its first step on its own, but Claude Code returns to your usual model after each reply, so setting it up front keeps the whole run on Sonnet.

3. **The first time only,** it asks for your resume. Give it the file's location (for example `~/Documents/resume.docx`). It then makes a file called `pii.txt` and asks you to open it in a text editor and type in your personal details: name, email, phone, employers, schools. Save it and tell Claude you're done. Claude swaps those details for placeholders like `[Name]` in its working copy. It never sees the real ones.

4. **Add job postings.** Open `postings/inbox.txt` and paste in each posting, or just its link, with a line of `=====` between them. Ten at a time is fine. Save, and tell Claude.

5. **Answer its questions.** Claude reads every posting and sends one numbered list of questions for the whole batch, such as "Have you used Epic charting?" or "OK to change 'taught patients' to 'patient education'?". Answer briefly (`E1 yes, 2.W1 no`). Anything you leave blank resolves the safe way.

6. **Collect your resumes.** Each job gets a folder under `applications/<date>/` with a ready-to-submit PDF named after you, plus a short summary of what changed and why.

If you close Claude partway through, run `/resume-ats-optimizer` again and it picks up
where it stopped.

## Your privacy

- Your resume, job postings, personal details, and finished applications stay on your computer. None of them are part of this project or uploaded anywhere by it.
- Your real name, email, phone number, employers, and schools live only in `pii.txt`. Claude is blocked from opening that file. A safety check runs before every action Claude takes, and it refuses any attempt to read the file or a finished PDF. The details are filled in by a program on your computer at the very end.
- The text of your resume and the postings is sent to Claude to do the tailoring, the same as anything else you share with Claude.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md). Free for personal use, including job hunting, and for education, research, and nonprofit organizations. You can change it and share your changes under the same terms. **Commercial use** (for example a recruiting firm, career coaching service, or resume product) **needs written permission**: open an issue on this repository to ask. This makes the project *source-available* rather than open source.

## For developers

How it works, the command-line tool, the privacy guard's design and limits, and the tests: [docs/DESIGN.md](docs/DESIGN.md). Where every external fact and dependency was verified: [docs/SOURCES.md](docs/SOURCES.md). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). Publishing a fork? Run `python3 hooks/scan-history.py` first. It checks every past commit for your real details.
