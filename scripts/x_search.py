#!/usr/bin/env python3
"""
Search X (Twitter) posts using xAI's Grok API and save results as Markdown.

Uses the OpenAI SDK with xAI's compatible endpoint. Requires XAI_API_KEY env var.

Usage:
  python x_search.py "AI agent frameworks" --days 7
  python x_search.py "product launch" --handles elonmusk,openai
  python x_search.py "knowledge graphs" --web-search -o output.md
"""

import argparse
import datetime
import os
import re
import sys

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4-1-fast-non-reasoning"


def sanitize_query(query):
    """Convert query string to a filesystem-safe slug.

    Args:
        query: Search query string.

    Returns:
        Sanitized string (max 50 chars, alphanumeric + underscores).
    """
    if not query or not query.strip():
        return "untitled"
    slug = re.sub(r'[^\w\s-]', '', query)
    slug = re.sub(r'[\s-]+', '_', slug).strip('_')
    if not slug:
        return "search"
    return slug[:50]


def build_output_path(query, output=None):
    """Determine the output file path.

    Args:
        query: Search query string.
        output: Explicit output path (optional).

    Returns:
        Absolute path for the output .md file.
    """
    if output:
        return os.path.abspath(output)
    date_str = datetime.date.today().isoformat()
    filename = f"x_search_{sanitize_query(query)}_{date_str}.md"
    return os.path.abspath(os.path.join("docs", filename))


def build_tools_config(days=7, handles=None, exclude_handles=None, web_search=False):
    """Build the tools configuration for the Grok API call.

    Args:
        days: Number of days to search back from today.
        handles: List of X handles to include (max 10).
        exclude_handles: List of X handles to exclude (max 10).
        web_search: Whether to enable web_search tool.

    Returns:
        List of tool configuration dicts.

    Raises:
        ValueError: If both handles and exclude_handles are provided.
    """
    if handles and exclude_handles:
        raise ValueError("Cannot use --handles and --exclude-handles together")

    to_date = datetime.date.today().isoformat()
    from_date = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

    x_tool = {
        "type": "x_search",
        "from_date": from_date,
        "to_date": to_date,
    }

    if handles:
        stripped = [h.lstrip('@') for h in handles]
        x_tool["allowed_x_handles"] = stripped[:10]
    elif exclude_handles:
        stripped = [h.lstrip('@') for h in exclude_handles]
        x_tool["excluded_x_handles"] = stripped[:10]

    tools = [x_tool]

    if web_search:
        tools.append({"type": "web_search"})

    return tools


def search_x(client, model, query, tools):
    """Execute search via Grok API.

    Args:
        client: OpenAI client configured for xAI.
        model: Model name.
        query: Search query.
        tools: Tools configuration list.

    Returns:
        API response object.
    """
    return client.responses.create(
        model=model,
        input=[{"role": "user", "content": query}],
        tools=tools,
    )


def format_response_as_markdown(response, query, from_date, to_date, model):
    """Format Grok API response as structured Markdown.

    Args:
        response: API response object.
        query: Original search query.
        from_date: Search start date.
        to_date: Search end date.
        model: Model used.

    Returns:
        Markdown string.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# X Search: {query}",
        "",
        f"- **Date range**: {from_date} — {to_date}",
        f"- **Model**: {model}",
        f"- **Searched at**: {now}",
        "",
        "## Results",
        "",
    ]

    # Extract text content from response output
    text_parts = []
    citations = []

    if hasattr(response, 'output') and response.output:
        for item in response.output:
            item_type = getattr(item, 'type', None)
            if item_type == "message":
                for content in getattr(item, 'content', []):
                    content_type = getattr(content, 'type', None)
                    if content_type == "output_text":
                        text_parts.append(getattr(content, 'text', ''))
                        for ann in getattr(content, 'annotations', []):
                            ann_type = getattr(ann, 'type', None)
                            if ann_type == "url_citation":
                                url = getattr(ann, 'url', '')
                                title = getattr(ann, 'title', url)
                                if url and url not in [c[1] for c in citations]:
                                    citations.append((title, url))

    if text_parts:
        lines.append("\n\n".join(text_parts))
    else:
        lines.append("No results found for this query.")

    if citations:
        lines.append("")
        lines.append("## Citations")
        lines.append("")
        for title, url in citations:
            lines.append(f"- [{title}]({url})")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Search X (Twitter) posts via Grok API and save as Markdown")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--days", type=int, default=7,
                        help="Number of days to search back (default: 7)")
    parser.add_argument("--handles", default=None,
                        help="Comma-separated X handles to include (max 10)")
    parser.add_argument("--exclude-handles", default=None,
                        help="Comma-separated X handles to exclude (max 10)")
    parser.add_argument("--web-search", action="store_true",
                        help="Also enable web search")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Grok model (default: {DEFAULT_MODEL})")
    parser.add_argument("-o", "--output", default=None,
                        help="Output .md path (default: docs/x_search_{{query}}_{{date}}.md)")
    parser.add_argument("-p", "--project", default=None,
                        help="Project name (passthrough for skill compatibility)")
    args = parser.parse_args()

    if not HAS_OPENAI:
        print("  [error] openai package not installed. Run: pip install openai",
              file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        print("  [error] XAI_API_KEY environment variable not set", file=sys.stderr)
        print("  [hint] Get your API key at https://console.x.ai/", file=sys.stderr)
        sys.exit(1)

    handles = args.handles.split(",") if args.handles else None
    exclude_handles = args.exclude_handles.split(",") if args.exclude_handles else None

    try:
        tools = build_tools_config(args.days, handles, exclude_handles, args.web_search)
    except ValueError as e:
        print(f"  [error] {e}", file=sys.stderr)
        sys.exit(1)

    output_path = build_output_path(args.query, args.output)

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.isdir(output_dir):
        print(f"  [error] Output directory does not exist: {output_dir}", file=sys.stderr)
        sys.exit(1)

    from_date = tools[0]["from_date"]
    to_date = tools[0]["to_date"]

    print(f"  [query]  {args.query}", file=sys.stderr)
    print(f"  [range]  {from_date} — {to_date}", file=sys.stderr)
    print(f"  [model]  {args.model}", file=sys.stderr)
    print(f"  [output] {output_path}", file=sys.stderr)

    client = OpenAI(base_url=XAI_BASE_URL, api_key=api_key)

    try:
        response = search_x(client, args.model, args.query, tools)
    except Exception as e:
        print(f"  [error] Grok API call failed: {e}", file=sys.stderr)
        sys.exit(1)

    markdown = format_response_as_markdown(response, args.query, from_date, to_date, args.model)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    char_count = len(markdown)
    line_count = markdown.count("\n") + 1
    print(f"  [done] {char_count} chars, {line_count} lines written", file=sys.stderr)

    # stdout: output path only (for pipeline consumption)
    print(output_path)


if __name__ == "__main__":
    main()
