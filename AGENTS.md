# Agent instructions

- Work inside the project root. Inspect relevant files and Git status before editing, and preserve existing work.
- Show changes to the student for review before any commit.
- Save each major prompt in `prompts/`, preserving existing numbering and files; record its observed results in `docs/verification.md`.
- Keep credentials out of source code, prompts, logs, and Git.

## Tools and dependencies

Whenever a tool or package is needed:

1. **CHECK:** Inspect the operating system, active Python interpreter, Node.js, package managers, dependency files, and lockfiles. Verify which relevant tools and packages are already available. Reuse the existing Python environment; do not create or recreate a virtual environment by default.
2. **TAKE ACTION:** For each missing dependency needed for the current task, explain its purpose, compatible version, exact command, installation target, and files it will change. Obtain student approval before installing software or changing a dependency manifest or lockfile.
3. **VERIFY:** Run the smallest relevant version, import, or build check in the intended environment. Record the command, expected result, and observed result in `docs/verification.md`, including checks still needed.

## SmokeTest and AutoLoop

A **SmokeTest** is a repeatable check that runs without source edits. Once a feature has a UI, it must start the local services, open a browser, perform a user operation, and compare its visible result with the expected result. Check stored data when relevant. Document one command to run the test and record failures as well as passes.

**AutoLoop** means inspecting a failed check, making the smallest in-scope correction, and rerunning it. Stop on success, after five correction cycles, or when further permission is needed.

At handoff, follow `handoffs/create-handoff.md`.
