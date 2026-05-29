"""Shared zygosity helpers (MRG-01a part 1; Plan §10.3).

Single source of truth for ``raw_variants.genotype`` no-call recognition across
every analysis module that reads raw genotypes. Step 61 (MRG-01a part 2) adopts
this helper across the thirteen in-scope modules; Step 62 reclassifies the
``_classify_genotype`` QC mapping to use it.

The recognition set is the union of every bespoke no-call filter present in
the analysis layer at the start of Phase 3, plus ``"??"`` — the canonical
"ambiguous" sentinel emitted by the ``flag_only`` merge strategy
(`backend/services/sample_merge.py`, Step 65) at discordant loci.

The membership set MUST stay aligned with Plan §10.3. Changing it is a
cross-module behavior change: every analysis module's no-call filter and the
``_classify_genotype`` QC stats route key off this exact recognition rule.
"""

from __future__ import annotations

_NO_CALL_SENTINELS: frozenset[str] = frozenset(
    {
        "",  # empty / whitespace-only after strip
        "--",  # legacy 23andMe no-call
        "??",  # merge ambiguity sentinel (Plan §10.3 / Step 65 flag_only)
        "-",  # haploid 23andMe no-call on X/Y for XY individuals
        "0",  # legacy single-allele zero
        "00",  # AncestryDNA no-call leakage / legacy PRS branch
        "II",  # indel — no ref/alt mapping; trait modules skip
        "DD",  # indel
        "DI",  # indel (either ordering)
        "ID",  # indel
    }
)


def is_no_call(genotype: str | None) -> bool:
    """Return True if a ``raw_variants.genotype`` is unscoreable.

    Recognizes (after ``strip()``):

    - ``None`` / empty / whitespace-only
    - ``"--"`` (legacy 23andMe no-call)
    - ``"??"`` (merge ambiguity sentinel — Plan §10.3)
    - ``"-"``  (haploid 23andMe no-call on X/Y for XY individuals)
    - ``"0"``, ``"00"`` (AncestryDNA no-call leakage / legacy PRS branch)
    - ``"II"``, ``"DD"``, ``"DI"``, ``"ID"`` (indel calls — no ref/alt mapping)

    Comparison is case-sensitive: post-Phase-1 ingestion always canonicalizes
    genotypes to uppercase, so lowercase inputs are treated as malformed
    (returns False) rather than silently absorbed.
    """
    if genotype is None:
        return True
    return genotype.strip() in _NO_CALL_SENTINELS
