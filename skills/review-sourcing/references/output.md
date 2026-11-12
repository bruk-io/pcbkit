# Sourcing review - the fixed format of sourcing.md

Read from SKILL.md's Output section. Write exactly these sections, in this order.

```markdown
# Sourcing review: <board> rev <rev> - <date>
Batch: <n> boards, <m> assembled, variants: <list>. Assembler: <name>.

## BOM risk table
| Line | Refs | Part number | Source checked (where, date) | Stock | Lifecycle | Alternate (compat, what changes) | Risk |
Risk is HIGH / MED / LOW / UNVERIFIED, with a one-line reason. Sorted HIGH first.

## Consolidation proposals
- <lines to merge> -> <proposed part>. Saves <n> lines. Behaviour change: none / <what>. Deliberate? <answer>.

## Assembly findings
- [BLOCKER|COST|CHECK] <ref or line>: <finding>. Evidence: <file, field, page, or check>. Action: <fix>.

## Variant matrix
| Group | Refs | Variant A | Variant B | ... | Jumper/strap state | Safe and testable when absent? |

## Unverified
Everything not checked live, and why.

## Written down
- <file and table>: <rows added or changed>
```
