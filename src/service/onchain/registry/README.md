# Project registry

`projects.json` is the operator's edit surface and the only place a project is
added. Adding one is a single object in `projects`; no code changes (requirement
V4, criterion A1). The build upserts this file into the entity store at the start
of every run, so an edit takes effect on the next build.

It is a tracked file rather than rows inserted by a CLI for one reason: the
frozen-threshold criterion (A9) is checked against version control, and a seed
list living only in a database has no history to check.

## Fields

| Field | Meaning |
| --- | --- |
| `chains[].chain_id` | the EVM chain id; the join key for everything else |
| `chains[].dexscreener_slug` | the provider's own name for the chain, which is not derivable from the id |
| `chains[].explorer_api` | the chain's Blockscout v2 base URL |
| `chains[].uniswap` | the five protocol addresses, verified on chain 2026-09-06; `src/service/onchain/config.py` holds the same values as a tripwire and validation rejects a mismatch |
| `chains[].lockers` | verified locker contracts; empty until one is verified, and the token-economics `lockers` field reads `unavailable` while it is |
| `projects[].pool_ref` | the operator's Dexscreener reference: a 32-byte pool id for a Uniswap v4 pool, a 20-byte pool address for v3. Never assumed to be a token or pair contract; the identity collector resolves it |
| `projects[].archetype` | selects whether a treasury field exists and how holders are derived. An unknown value is rejected at load |
| `projects[].sources` | operator-supplied sources, admitted as `admitted_by = 'registry'`. Phase 1a stores them and reads none of them |

## The seed four

Pool references are the operator's own Dexscreener links, recorded verbatim in
`MARKET-DATA/docs/tickets/in-progress/2026-09-05-onchain-analytics-long-term-vision.md`.
Touch Grass's four sources are the ones the same statement supplies; the other
three had none, and their published surfaces arrive in phase 1b through the
structural hop, not by being typed in here.

## Do not add `__init__.py` here

`registry.py` sits beside this directory, and `src.service.onchain.registry`
resolves to that module only because this directory is not a package. Adding an
`__init__.py` would silently redirect every import of the loader to an empty
package.
