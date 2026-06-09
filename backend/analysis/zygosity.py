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


# Watson–Crick complement for strand-flip resolution. 23andMe probes are
# reported on the chip's design strand, which is sometimes the reverse strand
# relative to the reference genome (and therefore relative to ClinVar's VCF,
# which is always on the ``+`` strand). When a genotype's alleles match neither
# the reference-strand ``{ref, alt}`` pair nor obviously, we retry against the
# complemented pair before giving up.
_COMPLEMENT: dict[str, str] = {"A": "T", "T": "A", "C": "G", "G": "C"}

# Public alias: ``backend.analysis.allele_match`` (and any future strand-aware
# caller) imports the complement map from here so there is exactly one copy. The
# private name is retained for the existing internal references below.
COMPLEMENT: dict[str, str] = _COMPLEMENT

# Zygosity vocabulary written to ``annotated_variants.zygosity`` (and consumed
# by the carrier / cancer / cardiovascular modules and the variant browser).
ZYG_HOM_REF = "hom_ref"
ZYG_HET = "het"
ZYG_HOM_ALT = "hom_alt"

# Zygosities for which the individual carries at least one copy of the ClinVar
# ALT (i.e. potentially clinically relevant). Modules gate findings on this so a
# homozygous-reference chip position never produces a "Pathogenic" finding.
CARRIED_ZYGOSITIES: frozenset[str] = frozenset({ZYG_HET, ZYG_HOM_ALT})


def _zygosity_from_alleles(alleles: set[str], ref: str, alt: str) -> str:
    """Classify a resolved allele set against a (ref, alt) pair."""
    has_ref = ref in alleles
    has_alt = alt in alleles
    if has_alt and has_ref:
        return ZYG_HET
    if has_alt:
        return ZYG_HOM_ALT
    return ZYG_HOM_REF


def classify_zygosity(genotype: str | None, ref: str | None, alt: str | None) -> str | None:
    """Resolve a genotype to ``hom_ref`` / ``het`` / ``hom_alt`` vs ClinVar ref/alt.

    This is the carriage test the pipeline previously lacked: a 23andMe chip
    reports a genotype at *every* probe regardless of whether the individual
    carries the variant, so a ClinVar "Pathogenic" record at that position must
    not be treated as a positive finding unless the genotype actually contains
    the ALT allele.

    Returns one of:

    - ``"hom_ref"`` — both alleles are the reference (variant NOT carried)
    - ``"het"``     — one ref, one alt (carrier)
    - ``"hom_alt"`` — both alleles are the alt (homozygous)
    - ``None``      — carriage cannot be determined and the caller must treat the
      variant as unscoreable rather than guess. This covers: no-call genotypes
      (see :func:`is_no_call`, includes the ``I``/``D`` indel codes), non-SNV
      ``ref``/``alt`` (indels / multi-base alleles have no A/C/G/T chip mapping),
      and genotypes whose alleles match neither the ``+`` strand ``{ref, alt}``
      pair nor its complement.

    Strand handling: the reference-strand comparison is tried first; if the
    alleles are explained only by the complemented pair the genotype is treated
    as reverse-strand. For palindromic SNPs (``A/T`` or ``C/G``) the two pairs
    are identical, so a homozygous call is taken at face value on the ``+``
    strand — the rare genuinely strand-ambiguous case, consistent with observed
    23andMe/ClinVar strand concordance.
    """
    if is_no_call(genotype):
        return None
    # ``is_no_call`` guarantees a non-empty, non-None string here.
    gt = genotype.strip().upper()  # type: ignore[union-attr]

    # Only single-base SNV ref/alt can be compared to chip A/C/G/T calls.
    # Indels / MNVs (multi-base or empty) are unscoreable on a genotyping chip.
    if not ref or not alt or len(ref) != 1 or len(alt) != 1:
        return None
    ref = ref.upper()
    alt = alt.upper()
    if ref not in _COMPLEMENT or alt not in _COMPLEMENT:
        return None

    # Parse the genotype into its alleles: haploid single base (e.g. on X/Y for
    # XY individuals) is treated as a homozygous diploid.
    if len(gt) == 1:
        alleles = {gt}
    elif len(gt) == 2:
        alleles = {gt[0], gt[1]}
    else:
        return None
    if any(a not in _COMPLEMENT for a in alleles):  # non-ACGT (e.g. "-")
        return None

    # Reference-strand comparison.
    if alleles <= {ref, alt}:
        return _zygosity_from_alleles(alleles, ref, alt)
    # Reverse-strand fallback.
    cref, calt = _COMPLEMENT[ref], _COMPLEMENT[alt]
    if alleles <= {cref, calt}:
        return _zygosity_from_alleles(alleles, cref, calt)
    # Alleles explained by neither strand (e.g. a different/triallelic variant):
    # carriage of the ClinVar ALT cannot be confirmed.
    return None
