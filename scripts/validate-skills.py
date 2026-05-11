#!/usr/bin/env python3
"""Validate SKILL.md files and marketplace.json for structural correctness."""

import json
import os
import re
import sys

SKILLS_DIR = "skills"
MARKETPLACE_FILE = ".claude-plugin/marketplace.json"
REQUIRED_FRONTMATTER = ["name", "description"]
REQUIRED_METADATA = ["version"]

errors = []
warnings = []


def err(msg):
    errors.append(f"  ERROR: {msg}")


def warn(msg):
    warnings.append(f"  WARN:  {msg}")


def parse_frontmatter(content):
    """Extract YAML frontmatter fields (name, description, metadata.*)."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None
    block = match.group(1)
    fields = {}
    # Parse top-level scalar fields
    for line in block.splitlines():
        m = re.match(r"^(\w+):\s*(.+)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    # Parse metadata sub-fields
    metadata = {}
    in_metadata = False
    for line in block.splitlines():
        if re.match(r"^metadata:", line):
            in_metadata = True
            continue
        if in_metadata:
            m = re.match(r"^\s+(\w+):\s*(.+)$", line)
            if m:
                metadata[m.group(1)] = m.group(2).strip()
            elif not line.startswith(" "):
                in_metadata = False
    if metadata:
        fields["metadata"] = metadata
    return fields


def validate_skill(skill_name):
    skill_dir = os.path.join(SKILLS_DIR, skill_name)
    skill_file = os.path.join(skill_dir, "SKILL.md")

    if not os.path.isfile(skill_file):
        err(f"{skill_name}: SKILL.md not found")
        return

    content = open(skill_file).read()

    fm = parse_frontmatter(content)
    if fm is None:
        err(f"{skill_name}: missing YAML frontmatter (expected --- block)")
        return

    # name matches directory
    if fm.get("name") != skill_name:
        err(f"{skill_name}: frontmatter 'name' is '{fm.get('name')}', expected '{skill_name}'")

    # required top-level fields
    for field in REQUIRED_FRONTMATTER:
        if not fm.get(field):
            err(f"{skill_name}: missing or empty frontmatter field '{field}'")

    # description not too short
    desc = fm.get("description", "")
    if desc and len(desc) < 20:
        warn(f"{skill_name}: description is very short ({len(desc)} chars)")

    # metadata fields
    metadata = fm.get("metadata", {})
    for field in REQUIRED_METADATA:
        if not metadata.get(field):
            err(f"{skill_name}: missing metadata.{field}")

    # version format x.y.z
    version = metadata.get("version", "")
    if version and not re.match(r"^\d+\.\d+\.\d+$", version):
        err(f"{skill_name}: metadata.version '{version}' is not semver (x.y.z)")

    # SKILL.md has at least one ## heading (has real content)
    if not re.search(r"^## ", content, re.MULTILINE):
        warn(f"{skill_name}: no ## section headings found in SKILL.md")


def validate_marketplace():
    if not os.path.isfile(MARKETPLACE_FILE):
        err(f"marketplace file not found: {MARKETPLACE_FILE}")
        return set()

    try:
        data = json.load(open(MARKETPLACE_FILE))
    except json.JSONDecodeError as e:
        err(f"marketplace.json is invalid JSON: {e}")
        return set()

    listed = set()
    for plugin in data.get("plugins", []):
        for skill_path in plugin.get("skills", []):
            # "./skills/ha-auth" → "ha-auth"
            listed.add(skill_path.rstrip("/").split("/")[-1])

    return listed


def main():
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))

    if not os.path.isdir(SKILLS_DIR):
        print(f"ERROR: '{SKILLS_DIR}' directory not found")
        sys.exit(1)

    skill_dirs = sorted(
        d for d in os.listdir(SKILLS_DIR)
        if os.path.isdir(os.path.join(SKILLS_DIR, d))
    )

    if not skill_dirs:
        print("ERROR: no skill directories found under skills/")
        sys.exit(1)

    print(f"Validating {len(skill_dirs)} skills: {', '.join(skill_dirs)}\n")

    for skill in skill_dirs:
        validate_skill(skill)

    marketplace_skills = validate_marketplace()
    for skill in skill_dirs:
        if skill not in marketplace_skills:
            warn(f"{skill}: not listed in {MARKETPLACE_FILE}")
    for skill in marketplace_skills - set(skill_dirs):
        err(f"marketplace references '{skill}' but no such skill directory exists")

    if warnings:
        print("Warnings:")
        for w in warnings:
            print(w)
        print()

    if errors:
        print("Errors:")
        for e in errors:
            print(e)
        print(f"\n{len(errors)} error(s) found — validation FAILED")
        sys.exit(1)

    print(f"All {len(skill_dirs)} skills passed validation.")


if __name__ == "__main__":
    main()
