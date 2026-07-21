# PSV Orchestration Flow

```mermaid
flowchart TD
    A([Input.xlsx]) --> B[Load & Normalize Rows\nstate · prov_type · first · last · lic_id · NPI_NO]

    B --> C{Route via\nboard_routing_master.csv}
    C -- no match --> OUT_M[manual channel\nreason: no_routing]
    C -- matched boards --> D

    B --> NPPES[NPPES Fetch\nnpiregistry.cms.hhs.gov\ncached 30d per NPI]
    NPPES --> NPPES_OUT[nppes channel\nevery row · flattened record + diff vs master]

    D[Build Attempt Plan\ncapability × non-empty fields]

    D --> L1

    subgraph MASTER_LADDER["Master Ladder  (boards × rungs)"]
        L1["Rung 1 — license_number"]
        L1 --> L2["Rung 2 — license_numeric_only\nstrip non-digits"]
        L2 --> L3["Rung 3 — license_first_last"]
        L3 --> L4["Rung 4 — license_and_last"]
        L4 --> L5["Rung 5 — license_and_first"]
        L5 --> L6["Rung 6 — first_and_last"]
        L6 --> L7["Rung 7 — last_name"]
        L7 --> L8["Rung 8 — first_name"]
    end

    L1 & L2 & L3 & L4 & L5 & L6 & L7 & L8 --> DIS

    subgraph DISAMBIG["Disambiguator  (per rung result set)"]
        DIS{Gate: first+license\nOR first+last?}
        DIS -- fail gate --> NEXT_RUNG[next rung]
        DIS -- pass gate\n1 candidate --> SCORE{Score ≥ threshold?}
        DIS -- pass gate\n>1 candidates --> NARROW

        SCORE -- yes --> PASS([Pass\nstandard channel])
        SCORE -- no --> NEXT_RUNG

        NARROW["Narrow in-memory\n1. numeric_lic + first\n2. first + last\n3. provider_type tiebreak"]
        NARROW -- 1 survives --> PASS
        NARROW -- still ambiguous --> ESC_AMB[escalate: ambiguous_after_narrowing]
    end

    NEXT_RUNG -- all rungs exhausted --> NPPES_RETRY

    subgraph NPPES_RETRY["NPPES Targeted Retry  (differing fields only)"]
        NR["Diff master vs NPPES\nfirst · last · license · other_names"]
        NR -- new values\nnot yet attempted --> L1
        NR -- nothing new\nto try --> EXHAUSTED
    end

    NPPES --> NR
    EXHAUSTED[both ladders exhausted] --> AI_GATE

    ESC_AMB --> AI_GATE

    subgraph AI_AGENT["AI Agent Fallback  (max 8 turns)"]
        AI_GATE{circuit\nbreaker open?}
        AI_GATE -- open --> OUT_AI_CB[ai_fallback channel\nreason: ai_circuit_breaker_open]
        AI_GATE -- closed --> AGENT["Agent loop\ntools: try_search · inspect_evidence\n       pick_candidate · report_site_drift · give_up"]
        AGENT -- pick_candidate --> PASS
        AGENT -- give_up / max turns --> OUT_AI[ai_fallback channel\nreason: structured code]
        AGENT -- report_site_drift --> DRIFT[Output/_drift/\nsite_drift_report.csv\nno auto-apply]
    end

    OUT_AI --> OUT_M2[manual channel\nunresolved rows]

    PASS --> STD_OUT[standard channel\nExcel + CSV\nmatch_method · fuzzy_score · attempts_used\nevidence_dir · npi_discrepancy_used]
```

---

## Output Channels

| Channel | Format | Contents |
|---|---|---|
| `Output/standard/{YYYY-MM}/` | Excel + CSV | Every row — canonical answer, provenance columns |
| `Output/nppes/{YYYY-MM}/` | CSV | Every row — NPPES record + diff vs master |
| `Output/ai_fallback/{YYYY-MM}/` | CSV | Rows that hit the agent — outcome + structured reason |
| `Output/manual/{YYYY-MM}/` | CSV | Unresolved rows — structured failure_reason |
| `Output/_drift/` | CSV (append) | Site drift reports filed by agent |

---

## Structured Failure Reasons

| Code | Trigger |
|---|---|
| `no_routing` | No board for `(state, prov_type)` in routing CSV |
| `no_records` | Every rung on every board returned zero records |
| `name_mismatch` | Records found but no candidate passed the name gate |
| `license_mismatch` | Name matched but license number disagreed |
| `provider_type_mismatch` | Name + license matched but prov_type misaligned |
| `ambiguous_after_narrowing` | Multiple candidates survived all narrowing steps |
| `ai_circuit_breaker_open` | AI endpoint repeatedly errored — skipped |
| `ai_max_turns_exceeded` | Agent ran 8 turns without committing |
| `ai_gave_up` | Agent called `give_up` — reason appended |

---

## Per-Rung Evidence

Every ladder rung captures a screenshot + HTML under:

```
Evidence/{YYYY-MM}/{state}/{source_id}/{ts}_{query}/
  search_results.html
  search_results.png
```

Signature dedup `(source_id, mode, normalized_query)` ensures no rung runs twice,
even when the NPPES retry loop re-enters the master ladder with new field values.
