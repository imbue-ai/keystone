import re
from pathlib import Path

from jinja2 import Template

from sculptor.cli.changelog.models import MergeCommit
from sculptor.cli.changelog.notion import categorize_commits


def clean_markdown_and_make_quote_blocks(text: str) -> str:
    """Remove markdown headings from text."""
    if not text:
        return text
    # Replace lines that start with # (markdown headings)
    lines = text.split("\n")
    stripped_lines = []
    for line in lines:
        # Remove heading markers from start of line
        stripped = re.sub(r"^#+\s+", "", line)
        stripped = re.sub(r"^>", "", stripped)
        stripped_lines.append("> " + stripped)
    return "\n".join(stripped_lines)


def generate_markdown_changelog(
    from_version: str,
    to_version: str,
    commits: list[MergeCommit],
    cut_time: str | None = None,
    template_path: Path | None = None,
) -> str:
    """Generate a markdown changelog from commits using a Jinja2 template."""
    if template_path is None:
        template_path = Path(__file__).parent / "changelog_template.md.jinja2"

    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    template_content = template_path.read_text()
    template = Template(template_content)
    template.globals["strip_headings"] = clean_markdown_and_make_quote_blocks
    categories = categorize_commits(commits)

    return template.render(
        start_version=from_version,
        end_version=to_version,
        cut_time=cut_time,
        features=categories["features"],
        improvements=categories["improvements"],
        bugs=categories["bugs"],
        no_linear_ticket=categories["no_linear_ticket"],
        no_label=categories["no_label"],
    )
