#!/usr/bin/env python3
"""Verify that every version-bearing location in this repo agrees.

The version number is duplicated across files (see the versioning rules in
CLAUDE.md) and has drifted twice before, which broke the CLI's own update
check. Checked locations, all of which MUST equal scripts/ha.py's
CLI_VERSION:

  scripts/ha.py                    CLI_VERSION constant (source of truth)
  skills/*/SKILL.md                metadata.version (one per skill)
  .claude-plugin/marketplace.json  metadata.version
  .codex-plugin/plugin.json        version
  .grok-plugin/plugin.json         version
  plugin.yaml                      version (Hermes)
  scripts/ha.py --version          runtime output

If HEAD carries an exact git tag (vX.Y.Z), the tag must match CLI_VERSION
too (CLAUDE.md: never push a tag whose commit doesn't carry the matching
CLI_VERSION). The tag check is skipped when HEAD is untagged so that
mid-development commits on main don't fail CI.

Usage:
    python3 scripts/check_version_sync.py

Exit status: 0 if every location agrees, 1 otherwise (with a per-file
report of the mismatches).
"""

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HA = ROOT / "scripts" / "ha.py"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def fail(messages):
    for line in messages:
        print(f"  MISMATCH  {line}", file=sys.stderr)
    print(
        "\nVersion sync FAILED. Every location listed above must carry the "
        "same version — see the versioning rules in CLAUDE.md.",
        file=sys.stderr,
    )
    sys.exit(1)


def cli_version():
    text = HA.read_text(encoding="utf-8")
    match = re.search(r'^CLI_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        fail([f"{HA.relative_to(ROOT)}: CLI_VERSION constant not found"])
    return match.group(1)


def skill_versions():
    skills_dir = ROOT / "skills"
    found = {}
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        # metadata.version lives (indented) inside the leading YAML frontmatter
        version = None
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                match = re.search(
                    r"^[ \t]*version:[ \t]*(\S+)[ \t]*$",
                    text[:end], re.MULTILINE,
                )
                if match:
                    version = match.group(1)
        if version is None:
            fail([f"{skill_md.relative_to(ROOT)}: no `version:` line in frontmatter"])
        found[skill_md.parent.name] = version
    if not found:
        fail(["skills/: no skills/*/SKILL.md found"])
    return found


def json_version(path, dotted_key):
    data = json.loads(path.read_text(encoding="utf-8"))
    node = data
    for key in dotted_key.split("."):
        if not isinstance(node, dict) or key not in node:
            fail([f"{path.relative_to(ROOT)}: key `{dotted_key}` not found"])
        node = node[key]
    return node


def yaml_version(path):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^version:\s*(\S+)\s*$", text, re.MULTILINE)
    if not match:
        fail([f"{path.relative_to(ROOT)}: no top-level `version:` line"])
    return match.group(1)


def runtime_version():
    result = subprocess.run(
        [sys.executable, str(HA), "--version"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        fail([f"ha.py --version exited {result.returncode}: {result.stderr.strip()}"])
    return result.stdout.strip()


def head_tag():
    result = subprocess.run(
        ["git", "describe", "--tags", "--exact-match", "HEAD"],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    return result.stdout.strip()[1:] if result.returncode == 0 else None


def main():
    errors = []
    reference = cli_version()
    if not VERSION_RE.match(reference):
        errors.append(f"scripts/ha.py CLI_VERSION={reference!r} is not X.Y.Z")

    checks = [
        ("scripts/ha.py --version", runtime_version()),
        (".claude-plugin/marketplace.json metadata.version",
         json_version(ROOT / ".claude-plugin" / "marketplace.json", "metadata.version")),
        (".codex-plugin/plugin.json version",
         json_version(ROOT / ".codex-plugin" / "plugin.json", "version")),
        (".grok-plugin/plugin.json version",
         json_version(ROOT / ".grok-plugin" / "plugin.json", "version")),
        ("plugin.yaml version", yaml_version(ROOT / "plugin.yaml")),
    ]
    for skill_name, version in skill_versions().items():
        checks.append((f"skills/{skill_name}/SKILL.md metadata.version", version))

    for location, version in checks:
        if version != reference:
            errors.append(f"{location} = {version!r}, expected {reference!r}")

    tag = head_tag()
    if tag is not None and tag != reference:
        errors.append(
            f"git tag v{tag} on HEAD does not match CLI_VERSION {reference!r} "
            "(CLAUDE.md: the tag and CLI_VERSION must match exactly)"
        )

    if errors:
        fail(errors)

    suffix = f" (HEAD tag v{tag} matches)" if tag else " (HEAD untagged — tag check skipped)"
    print(f"Version sync OK: {reference} across {len(checks)} locations{suffix}")


if __name__ == "__main__":
    main()
