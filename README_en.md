# Sisho (司書)

[日本語](./README.md)

A librarian MCP (Model Context Protocol) server for Magic: The Gathering.

It connects AI assistants directly to a verified database of cards (including official Japanese names), Comprehensive Rules, official rulings, and real-world deck statistics via standard MCP tools.
Instead of relying on the model's unreliable memory or second-hand web sources, assistants retrieve verified primary data directly through tool calls.

The server configuration key is `sisho`. The name comes from 司書 (*shisho*), the word official Japanese Magic cards use for "Librarian" (as in *Cogwork Librarian*).

| Document | Audience & Contents |
| --- | --- |
| [docs/導入方法.md](./docs/導入方法.md) | **For Users**: Detailed connection guide *(Japanese)* |
| [docs/SETUP.md](./docs/SETUP.md) | **For Builders**: Local DB setup and self-hosting / operations guide *(Japanese)* |
| [docs/ENGINEERING.md](./docs/ENGINEERING.md) | **For Developers**: Design philosophy and architectural details *(Japanese)* |
| [hooks/README.md](./hooks/README.md) | **For Claude Code Users**: Stop hook for automated card name verification *(Japanese)* |

---

## Available Tools

The following tools are available to connected clients:

| Tool | Returns |
| --- | --- |
| `search_mtg_cards` | Search cards by name (Japanese/English, partial match) or oracle text keywords. Ranked by exact name match first, followed by EDHREC popularity. Can filter by legality in specified formats. |
| `lookup_mtg_rule` | Look up Comprehensive Rules by rule number or English keywords (includes the glossary). |
| `get_card_rulings` | Retrieve official Magic rulings (issued by Wizards of the Coast) by card name. |
| `find_partner_cards` | Return co-occurring cards commonly included in the same deck, aggregated from real-world decklists. Supports multiple formats (Standard, Pioneer, Modern, Legacy, Premodern, Pauper, Vintage, Duel Commander, Commander, Preconstructed), prioritizing the last 90 days with full-history fallback. For 60-card formats, sideboard options are also included. |
| `draft_pack_stats` | Batch fetch 17Lands stats—such as GIH WR (win rate when the card was in hand) and ALSA (average pick number when last seen)—for multiple cards (1–20 cards) in a draft pack in a single call. If a name does not match exactly, suggests close candidates from the set. |
| `query_mtg_database` | Execute read-only SQL queries directly for custom aggregations not covered by dedicated tools (SELECT/WITH only, single statement, 10s timeout, max 50 rows). |
| `verify_answer` | Takes the full text of a drafted answer, matches card names against the database to detect hallucinations/unregistered names, and returns a corrected version with canonical naming. |
| `mtg_probability` | Calculate exact hypergeometric probabilities for decks (opening hands, draws by turn t, consecutive land drops, 2-card combos, mana sources) using database SQL functions. Returns the exact formula and assumptions to prevent LLM arithmetic errors. |
| `find_combos` | Query the public Commander Spellbook API with a list of card names (up to a full deck) to find combos you can assemble now, combos one card away, and combos that need one more color, complete with prerequisites and source URLs (real-time query, source: Commander Spellbook). |
| `describe_mtg_tables` | List database tables, column names, and data types for inspecting schema before writing custom SQL. |
| `mtg_rag_health` | Check database connectivity, row counts, and data freshness of major tables. |

10 out of 11 tools connect directly to a local PostgreSQL database (`find_combos` queries an external API on demand) without calling any LLMs or vector search pipelines. Primary tool responses complete in under 1 second (local benchmarks, excluding heavy co-occurrence aggregations and external API calls).

To prevent AI assistants from mistranslating card names into Japanese, tools return card names in a standardized format: `《Japanese Name/English Name》`. Cards without an official Japanese print return only the English name with a note (`（日本語版なし）`).
This principle of "enforcing correctness via structured return values rather than prompt instructions" and its supporting benchmarks are detailed in the [docs/bench/README.md](./docs/bench/README.md) summary and [DESIGN.md](./DESIGN.md).

---

## Data

No raw data is included in this repository.
The repository contains scripts for constructing and updating the database, as well as the MCP server implementation.
For table schemas and row counts, see [DATA_MODEL.md](./DATA_MODEL.md). For data sources, licensing, and attribution, refer to [docs/DATA_SOURCES.md](./docs/DATA_SOURCES.md).

---

## Usage

1. **Get your connection URL**: Open the [issuance page](https://sisho.tailf8ef32.ts.net/issue) and click the **［発行する］** (Issue) button to generate your personal URL. No authentication or registration is required. Treat this URL as a private key.
2. **Connect to your AI client**:
   - **claude.ai**: Go to **Settings** → **Connectors** → **Add Custom Connector**, enter `sisho` as the name, and paste your URL. (Works across browser, desktop, and mobile apps).
   - **ChatGPT**: Requires a paid subscription (Plus, Pro, Business, Enterprise, or Education; web only, not available on Free). In **Settings** → **Security and login**, enable **Developer mode**. Open the **Plugins** screen, click the plus (+) button to add an MCP app, enter a name and description (e.g., `sisho`), paste your URL into the **Connection** field, check the acknowledgment box, and create the app. UI labels may vary.
   - **Claude Code (CLI)**: Run `claude mcp add --transport http sisho <YOUR_URL>`.

For full step-by-step documentation in Japanese, see [docs/導入方法.md](./docs/導入方法.md).

---

## License

The source code is licensed under the MIT License. Raw card and game data are not included. Magic: The Gathering is a registered trademark of Wizards of the Coast LLC, and this project is an unofficial, non-commercial Fan Content.

Sisho is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.

Limited format statistics are derived from the Public Datasets provided by 17Lands (https://www.17lands.com/) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This project provides aggregated values derived from these datasets; 17Lands has not approved or endorsed this project.

## Usage Notes

- **Rate Limits**: Limited to 60 requests/minute per connection URL and 300 requests/minute across the entire server (exceeding returns HTTP 429). See [docs/PUBLIC_SERVER.md](./docs/PUBLIC_SERVER.md) for details.
- **Input Logging**: Inputs sent to tools (search terms, SQL queries, card names, etc.) are logged on the server for troubleshooting and quality improvements (purged after approximately 5 weeks; never shared with third parties).
- **Client IP Logging**: Client IPs are not logged during normal tool usage or when viewing the connection URL issuance page. IPs are recorded solely to prevent abuse during URL issuance, rejection of unauthorized origins, and rate limiting by client IP (purged after approximately 5 weeks).
- **Disclaimer**: AI assistants generate the final response text; Sisho provides verified primary data. No prior user registration is required, but the service is provided as-is, as an experiment, without warranties of any kind.
- **URL Management**: Treat your issued connection URL as a private key and do not share it publicly. If lost, you can re-issue a new URL at the issuance page at any time.
- **Inquiries**: For bug reports, feature requests, or questions, please use our [Inquiry Form](https://docs.google.com/forms/d/e/1FAIpQLSdiAwOzxL4aCORh-vy8lnG0vOFpW-_0FWAkVK0ofviURyqybQ/viewform) (contact email optional) or open an issue on GitHub.
