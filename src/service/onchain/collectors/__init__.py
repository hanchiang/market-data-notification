"""The four deterministic dossier collectors (phase 1a).

Each module exposes one `collect(context) -> SectionResult`. Nothing here writes
a build row, closes a run or sends anything: the builder owns that, so a
collector that raises costs its own section and nothing else (A3).
"""
