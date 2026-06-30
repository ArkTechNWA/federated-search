"""Format federated search results as readable markdown."""

from __future__ import annotations

from typing import Any


def format_results(data: dict[str, Any]) -> str:
    """Convert fed_search JSON results into markdown tables."""
    query = data.get("query", "")
    results = data.get("results", [])
    banks_queried = data.get("banks_queried", [])
    total = data.get("total", 0)
    hint = data.get("hint")

    if not results:
        lines = [f"## {query} — no results"]
        if hint:
            lines.append(f"\n> {hint}")
        return "\n".join(lines)

    # Group results by bank
    by_bank: dict[str, list[dict[str, Any]]] = {}
    bank_labels: dict[str, str] = {}
    for r in results:
        bank = r["bank"]
        by_bank.setdefault(bank, []).append(r)
        bank_labels[bank] = r.get("bank_label", bank)

    bank_counts = {b["id"]: b.get("result_count", 0) for b in banks_queried}

    lines = [f"## {query} — {total} results across {len(by_bank)} banks"]

    for bank_id, bank_results in by_bank.items():
        label = bank_labels[bank_id]
        count = bank_counts.get(bank_id, len(bank_results))
        lines.append(f"\n### {label} ({count})")

        first = bank_results[0]
        source_type = first.get("source_type", "")

        if source_type == "entity":
            lines.append("| Relevance | Entity | Type | Drill |")
            lines.append("|-----------|--------|------|-------|")
            for r in bank_results:
                rel = f"{r['relevance']:.2f}"
                etype = r.get("metadata", {}).get("entity_type", "")
                snippet = r["snippet"][:60]
                if snippet.startswith("["):
                    snippet = ""
                drill = f"`{r['drill']}`"
                title = r["title"]
                if snippet:
                    lines.append(f"| {rel} | **{title}** | {etype} | {drill} |")
                    lines.append(f"|     | _{snippet}_ |||")
                else:
                    lines.append(f"| {rel} | **{title}** | {etype} | {drill} |")

        elif source_type == "chunk":
            lines.append("| Relevance | Session | Snippet |")
            lines.append("|-----------|---------|---------|")
            for r in bank_results:
                rel = f"{r['relevance']:.3f}"
                title = r["title"]
                snippet = r["snippet"][:100].replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {rel} | {title} | {snippet} |")

        elif source_type in ("web", "synthesis"):
            for r in bank_results:
                if r["source_type"] == "synthesis":
                    lines.append(f"\n**AI Summary:** {r['snippet']}\n")
                else:
                    url = r.get("metadata", {}).get("url", r.get("drill", ""))
                    lines.append(f"- [{r['title']}]({url})")
                    if r["snippet"]:
                        lines.append(f"  _{r['snippet'][:120]}_")

        else:
            # Generic fallback
            lines.append("| Relevance | Title | Snippet |")
            lines.append("|-----------|-------|---------|")
            for r in bank_results:
                rel = f"{r['relevance']:.3f}"
                snippet = r["snippet"][:100].replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {rel} | {r['title']} | {snippet} |")

    # Bank status summary
    statuses = []
    for b in banks_queried:
        status = b.get("status", "?")
        icon = "ok" if status == "ok" else "err"
        statuses.append(f"{b['label']}={icon}({b.get('result_count', '?')})")
    lines.append(f"\n_Banks: {', '.join(statuses)}_")

    if hint:
        lines.append(f"\n> {hint}")

    return "\n".join(lines)


def format_banks(banks: list[dict[str, Any]]) -> str:
    """Format fed_banks results as markdown."""
    lines = ["## Registered Memory Banks", ""]
    lines.append("| Priority | Bank | Type | Default | Status | Description |")
    lines.append("|----------|------|------|---------|--------|-------------|")
    for b in banks:
        default = "yes" if b["default"] else "—"
        status = b["status"]
        lines.append(f"| {b['priority']} | **{b['id']}** | {b['type']} | {default} | {status} | {b['description']} |")
    return "\n".join(lines)
