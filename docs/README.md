# Documentation index

This directory is the living design record of Repo Assistant. **It must always reflect the current state of the project** — documentation updates ship in the same change set as the decisions they describe.

| Document | Purpose | Update trigger |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design: components, module responsibilities, data flow, data model, pipelines, scalability, security, observability | Any structural or pipeline change |
| [ROADMAP.md](ROADMAP.md) | Phased plan, milestones, exit criteria, implementation priorities | Phase transitions, scope changes |
| [EVALUATION.md](EVALUATION.md) | Evaluation methodology, datasets, metrics, CI gates | Any change to how quality is measured |
| [RISKS.md](RISKS.md) | Risk register with likelihood/impact/mitigation | New risks discovered, mitigations landed |
| [UAT.md](UAT.md) | Manual user acceptance test plan covering every working flow, with pass criteria | New user-facing flow ships, or an existing flow's surface changes |
| [adr/](adr/README.md) | Architecture Decision Records | New significant decision, or superseding an old one |

## ADR policy

- One decision per record, numbered sequentially, format: Status / Context / Decision / Alternatives considered / Consequences.
- ADRs are immutable history: to change a decision, write a new ADR that supersedes the old one and update the old record's Status line.
- A decision earns an ADR when it is expensive to reverse (storage engine, provider, data model) or shapes many modules (chunking strategy, reasoning architecture).
