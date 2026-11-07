# Contributing to Claude Progress Bar

Thanks for your interest! Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

Open an issue and include:

- what you expected and what the bar showed (a screenshot helps)
- your Windows version and whether PowerShell 7 (`pwsh`) is installed
- the relevant lines from `%LOCALAPPDATA%\ClaudeProgressBar\hook_events.log` and `hook_errors.log`

The event log contains your project and task names, so remove anything private before pasting it.

## Sending a pull request

1. Fork the repository and create a branch for your change.
2. Keep each pull request small and focused on one thing. Explain what it changes and why.
3. Say how you tested it: Windows version, PowerShell 7 and/or the built-in Windows PowerShell 5.1, and what you ran.
4. Keep both PowerShell versions working: no PowerShell 7-only syntax (`??`, ternary `? :`, `&&` / `||`) in `progress_window.ps1` or `progress_job.ps1`.
5. Bump `version` in `.claude-plugin/plugin.json` only if the maintainer asks you to.

## Ground rules

This plugin runs automatically on every command Claude Code runs, on every machine that installs it, so changes to what it does are reviewed carefully:

- **No network access, downloads, telemetry or new dependencies** without discussing it in an issue first. The program works entirely on the local machine and should stay that way.
- **The hook must never block or slow Claude Code.** `scripts/hook.py` has to return quickly and must not fail loudly.
- **Keep runtime data out of the repository.** Logs and task files hold users' project and task names.
- Clear, readable code over clever code. A reviewer has to understand every changed line before it can be merged.

## Ideas that would be welcome

- A macOS version (a menu-bar app reading the same task files)
- More progress-log formats for popular tools
- Translations of the window text

## Licence

By contributing, you agree that your contribution is released under the project's [MIT licence](LICENSE).
