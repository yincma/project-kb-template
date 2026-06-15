# Obsidian Curator Layer

This layer turns raw project materials into human-readable Obsidian notes that can also be indexed by Project KB.

## Boundary

- Raw files stay in `sources/`.
- Curated Markdown notes live in `docs/`.
- `docs/` is the Obsidian Vault and the curated source of truth.
- LanceDB is only an index and can be rebuilt.
- This layer is not a Proposal Agent and not a consulting Agent.

## Recommended Flow

1. Put original files into `sources/`.
2. Build the raw index with `kb/config.raw.yaml`.
3. Use raw index search results to draft Obsidian notes.
4. Keep structured `source_refs` in every generated note.
5. Mark unsupported statements as `Assumption` or `Evidence Gap`.
6. Human reviewers move notes to `status: reviewed`.
7. Rebuild the curated index from `docs/`.

## Raw Source Categories

- `sources/10_client_inputs/`: RFPs, emails, meeting notes, client decks.
- `sources/20_our_capabilities/`: capability decks, service descriptions, differentiators.
- `sources/30_case_studies/`: case studies, credentials, success stories.
- `sources/40_delivery_assets/`: plans, templates, methodology assets.
- `sources/50_commercial/`: pricing, commercials, scope assumptions.
- `sources/60_compliance/`: legal, security, data, risk, compliance material.

## Curated Vault Areas

- `docs/10_Clients/`
- `docs/20_Requirements/`
- `docs/30_Capabilities/`
- `docs/40_Case_Studies/`
- `docs/50_Delivery/`
- `docs/60_Methodology/`
- `docs/70_Commercial_Risk/`
- `docs/90_Proposal_Blocks/`

Use `docs/99_Inbox/` only for temporary drafts. It is excluded from the curated index.
