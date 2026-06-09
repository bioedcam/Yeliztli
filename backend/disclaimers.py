"""Hardcoded disclaimer text for Yeliztli.

All disclaimer, gate, and carrier status text lives here.
Referenced by the setup wizard (global disclaimer) and analysis modules
(APOE gate, carrier status, cancer, cardiovascular).
"""

# ── Global first-launch disclaimer ────────────────────────────────────

GLOBAL_DISCLAIMER_TITLE = "Important Information About Yeliztli"

GLOBAL_DISCLAIMER_TEXT = """\
Yeliztli is an educational and research tool designed to help you \
explore your personal genomic data. It is NOT a medical device and has \
NOT been reviewed or approved by the FDA or any regulatory authority.

Please read and understand the following before proceeding:

1. **Not a diagnostic tool.** The information provided by Yeliztli \
is for educational and research purposes only. It should not be used to \
diagnose, treat, cure, or prevent any disease or medical condition.

2. **Not a substitute for professional medical advice.** Always consult \
a qualified healthcare provider or certified genetic counselor before \
making any medical decisions based on genetic information. Do not \
disregard professional medical advice or delay seeking it because of \
something you have read in this application.

3. **Variant interpretation has limitations.** Genetic variant \
classifications change over time as scientific understanding evolves. \
A variant classified as "benign" today may be reclassified in the \
future, and vice versa. Yeliztli uses publicly available databases \
that may not reflect the most current scientific consensus.

4. **Genotyping chip limitations.** Consumer genotyping chips (such as \
23andMe) test only a subset of genetic variants. A negative result does \
not mean you do not carry a particular variant — it may simply not have \
been tested. Clinical-grade genetic testing is required for definitive \
diagnostic results.

5. **Population-specific considerations.** Risk scores and frequency \
data may not be equally accurate across all ancestral populations. Many \
genetic studies have been conducted primarily in populations of European \
descent, which may limit the applicability of results to other groups.

6. **Privacy responsibility.** You are responsible for the security of \
your own genetic data. Yeliztli runs locally on your computer and \
does not transmit your genetic data to external servers (except for \
optional PubMed literature lookups which send gene names only, never \
variant data).

7. **Emotional preparedness.** Genetic information may reveal unexpected \
findings about health risks, carrier status, or ancestry. Consider \
whether you are prepared to receive potentially sensitive information \
before proceeding.

By clicking "I Understand and Accept," you acknowledge that you have \
read and understood these limitations and agree to use Yeliztli \
solely for educational and research purposes.\
"""

GLOBAL_DISCLAIMER_ACCEPT_LABEL = "I Understand and Accept"

# ── APOE opt-in disclosure gate ────────────────────────────────────────

APOE_GATE_TITLE = "APOE Genetic Information Disclosure"

APOE_GATE_TEXT = """\
You are about to view information about your APOE genotype. The APOE \
gene has variants (particularly the e4 allele) that have been associated \
with increased risk of late-onset Alzheimer's disease and cardiovascular \
conditions.

**Important considerations before viewing:**

- Having an APOE e4 allele does NOT mean you will develop Alzheimer's \
disease. Many people with e4 never develop the condition, and many \
people without e4 do.

- APOE genotype is only one of many factors that influence disease risk. \
Lifestyle, environment, and other genetic factors also play significant \
roles.

- This information may cause significant emotional distress. You may \
wish to have a support person available or to consult with a genetic \
counselor before viewing.

- This result is based on a consumer genotyping chip and is NOT a \
clinical diagnostic test.

**Resources:**
- National Institute on Aging: https://www.nia.nih.gov/health/alzheimers-causes-and-risk-factors/alzheimers-disease-genetics-fact-sheet
- Alzheimer's Association: https://www.alz.org/alzheimers-dementia/what-is-alzheimers/causes-and-risk-factors/genetics
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/

This gate cannot be dismissed. You must actively choose to view or skip \
APOE information each time you access this section.\
"""

APOE_GATE_ACCEPT_LABEL = "I Understand — Show My APOE Results"
APOE_GATE_DECLINE_LABEL = "Not Now — Skip APOE Results"

# ── Carrier status disclaimer ──────────────────────────────────────────

CARRIER_STATUS_DISCLAIMER_TITLE = "About Carrier Status Results"

CARRIER_STATUS_DISCLAIMER_TEXT = """\
Carrier status results indicate whether you carry one copy (heterozygous) \
of a variant associated with a genetic condition. Carriers typically do \
not show symptoms of the condition themselves.

**This information is most relevant in a reproductive context.** If both \
partners carry a variant in the same autosomal recessive gene, there is \
a 25% chance with each pregnancy that the child will be affected by the \
condition. This is the basis of carrier screening in family planning.

**Please understand the following before reviewing:**

1. **Carrier ≠ affected.** Being a carrier means you have one working \
copy and one non-working copy of a gene. For autosomal recessive \
conditions (such as Cystic Fibrosis, Sickle Cell Disease, Tay-Sachs, \
Gaucher Disease, and Spinal Muscular Atrophy), carriers are typically \
healthy and unaffected.

2. **BRCA1/BRCA2 are a special case.** These genes follow autosomal \
dominant inheritance for cancer predisposition. A single pathogenic \
variant confers personal cancer risk (see the Cancer module) AND \
reproductive carrier risk. Both perspectives are shown in Yeliztli \
with distinct framing.

3. **Genotyping chip limitations.** Consumer genotyping chips test only \
a subset of known variants in each gene. A negative carrier result does \
NOT guarantee that you are not a carrier. Clinical-grade carrier \
screening (expanded carrier panels with full gene sequencing) is \
recommended for comprehensive reproductive planning.

4. **Population-specific carrier frequencies.** Some conditions are more \
common in certain ancestral populations (e.g., Tay-Sachs in Ashkenazi \
Jewish populations, Sickle Cell in African descent populations). The \
carrier panel used here is not population-specific — consult a genetic \
counselor for ancestry-informed screening recommendations.

5. **Professional genetic counseling is recommended.** If you have a \
carrier finding, a certified genetic counselor can help you understand \
the implications for family planning, discuss partner testing options, \
and explain reproductive alternatives.

**Resources:**
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/
- ACOG Carrier Screening: https://www.acog.org/clinical/clinical-guidance/committee-opinion/articles/2017/03/carrier-screening-for-genetic-conditions
- MedlinePlus — Genetic Testing: https://medlineplus.gov/genetictesting.html\
"""

# ── Per-gene carrier display notes ────────────────────────────────────

CARRIER_GENE_NOTES: dict[str, str] = {
    "CFTR": (
        "Cystic Fibrosis is the most common life-limiting autosomal "
        "recessive condition in people of European descent. Carrier "
        "frequency is approximately 1 in 25 in this population."
    ),
    "HBB": (
        "Variants in HBB can cause Sickle Cell Disease or "
        "Beta-Thalassemia depending on the specific variant. Carrier "
        "frequency is highest in populations from Africa, the "
        "Mediterranean, Middle East, and South Asia."
    ),
    "GBA": (
        "GBA variants are associated with Gaucher Disease. Carrier "
        "frequency is approximately 1 in 15 in Ashkenazi Jewish "
        "populations. GBA carrier status has also been associated with "
        "a modestly increased risk of Parkinson's disease, though this "
        "is a research finding and not a clinical diagnosis."
    ),
    "HEXA": (
        "HEXA variants are associated with Tay-Sachs Disease. Carrier "
        "frequency is approximately 1 in 30 in Ashkenazi Jewish "
        "populations and 1 in 300 in the general population."
    ),
    "BRCA1": (
        "BRCA1 is a dual-role gene: a pathogenic variant confers both "
        "personal cancer predisposition (autosomal dominant) and "
        "reproductive carrier risk. See the Cancer module for disease "
        "risk framing."
    ),
    "BRCA2": (
        "BRCA2 is a dual-role gene: a pathogenic variant confers both "
        "personal cancer predisposition (autosomal dominant) and "
        "reproductive carrier risk. See the Cancer module for disease "
        "risk framing."
    ),
    "SMN1": (
        "SMN1 variants are associated with Spinal Muscular Atrophy. "
        "Consumer genotyping chips cannot reliably detect SMN1 copy "
        "number variations, which are the most common cause of SMA. "
        "Results for this gene should be interpreted with extra caution."
    ),
}

# ── Cancer module disclaimer ─────────────────────────────────────────

CANCER_DISCLAIMER_TITLE = "About Cancer Predisposition Results"

CANCER_DISCLAIMER_TEXT = """\
This section reports genetic variants associated with hereditary cancer \
predisposition syndromes. These results are based on a curated panel of \
28 genes with established links to cancer risk.

**Please understand the following before reviewing:**

1. **Predisposition is not diagnosis.** Carrying a pathogenic variant in \
a cancer predisposition gene means you may have an increased lifetime \
risk for certain cancers. It does NOT mean you have cancer or will \
develop cancer. Many carriers never develop the associated condition.

2. **Absence of findings does not mean absence of risk.** Consumer \
genotyping chips test only a small fraction of known variants in these \
genes. A negative result here does NOT rule out hereditary cancer risk. \
Clinical-grade genetic testing (full gene sequencing and deletion/ \
duplication analysis) is required for comprehensive assessment.

3. **Polygenic Risk Scores are research-grade.** The PRS results in this \
module are derived from published GWAS data and are presented for \
educational purposes only. They reflect statistical associations across \
populations and may not accurately predict individual risk. PRS results \
should never be used to make clinical decisions.

4. **Variant classification evolves.** ClinVar classifications can change \
as new evidence emerges. A variant classified as pathogenic today may be \
reclassified, and variants not flagged now may be flagged in the future.

5. **Professional guidance is essential.** If you have a pathogenic or \
likely pathogenic finding in this module, consult a certified genetic \
counselor or medical geneticist for proper risk assessment, management \
recommendations, and discussion of implications for family members.

**Resources:**
- National Cancer Institute Genetics: https://www.cancer.gov/about-cancer/causes-prevention/genetics
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/
- FORCE (Facing Our Risk of Cancer Empowered): https://www.facingourrisk.org/\
"""

# ── Cardiovascular module disclaimer ───────────────────────────────

CARDIOVASCULAR_DISCLAIMER_TITLE = "About Cardiovascular Genetic Results"

CARDIOVASCULAR_DISCLAIMER_TEXT = """\
This section reports genetic variants associated with cardiovascular \
conditions including familial hypercholesterolemia (FH), \
cardiomyopathies, channelopathies, and lipid metabolism disorders. \
Results are based on a curated panel of 16 genes with established \
links to cardiovascular risk.

**Please understand the following before reviewing:**

1. **Genetic predisposition is not diagnosis.** Carrying a pathogenic \
variant in a cardiovascular gene means you may have an increased risk \
for the associated condition. It does NOT mean you currently have or \
will develop heart disease.

2. **Familial Hypercholesterolemia (FH).** FH is one of the most \
common genetic conditions affecting cholesterol metabolism. Early \
identification and treatment with lipid-lowering therapy can \
significantly reduce cardiovascular risk. If an FH-associated variant \
is identified, discuss cholesterol management with your physician.

3. **Absence of findings does not mean absence of risk.** Consumer \
genotyping chips test only a subset of known variants. A negative \
result does NOT rule out hereditary cardiovascular conditions. \
Clinical-grade genetic testing is required for comprehensive assessment.

4. **Variant classification evolves.** ClinVar classifications can \
change as new evidence emerges. A variant classified as pathogenic \
today may be reclassified in the future.

5. **Professional guidance is essential.** If you have a pathogenic or \
likely pathogenic finding, consult a cardiologist, certified genetic \
counselor, or medical geneticist for proper risk assessment and \
management recommendations.

**Resources:**
- Family Heart Foundation: https://familyheart.org/
- American Heart Association: https://www.heart.org/en/health-topics/cholesterol
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Hereditary haemochromatosis (HFE) module ─────────────────────────────────

HEMOCHROMATOSIS_DISCLAIMER_TITLE = "About Hereditary Haemochromatosis Results"

HEMOCHROMATOSIS_DISCLAIMER_TEXT = """\
This section reports the HFE genotype (C282Y / H63D) associated with hereditary \
haemochromatosis — a treatable disorder of iron metabolism.

**Please understand the following before reviewing:**

1. **Genotype is not diagnosis, and penetrance is reduced and sex-dependent.** \
Even C282Y homozygotes — the highest-risk genotype — do not all develop disease. \
Cumulative haemochromatosis diagnosis by age 80 was ~56% in men and ~41% in \
women, and most homozygotes are undiagnosed. Compound C282Y/H63D and single \
carriers are low-penetrance.

2. **It is treatable.** When iron overload does occur, it is managed effectively \
with phlebotomy. A pathogenic genotype is a reason to discuss iron studies \
(ferritin, transferrin saturation) with a clinician — not a reason to assume disease.

3. **Ancestry context.** Prevalence and penetrance figures come from \
European-ancestry populations; C282Y is near-absent in East Asian and \
sub-Saharan African ancestry.

4. **A negative result does not rule out iron overload.** The array tests only \
C282Y and H63D; rare HFE and non-HFE iron-overload variants are not interrogated.

5. **Professional guidance.** Discuss results with your physician or a genetic \
counselor before any medical action; confirm in a CLIA/accredited laboratory.

**Resources:**
- American Hemochromatosis Society: https://www.americanhs.org/
- Iron Disorders Institute: https://irondisorders.org/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Inherited thrombophilia (Factor V Leiden / Prothrombin) module ──────────

THROMBOPHILIA_DISCLAIMER_TITLE = "About Inherited Thrombophilia Results"

THROMBOPHILIA_DISCLAIMER_TEXT = """\
This section reports two well-established inherited thrombophilia variants — \
Factor V Leiden (rs6025) and Prothrombin G20210A (rs1799963) — that modestly \
raise the risk of venous blood clots (venous thromboembolism, VTE).

**Please understand the following before reviewing:**

1. **Relative risk is not absolute risk.** These variants raise *relative* risk \
(roughly 3–5× for a single Factor V Leiden allele, 2–3× for Prothrombin, ~5× for \
both together), but the *absolute* lifetime risk for most carriers stays low — \
only about 10% of Factor V Leiden carriers ever have a clot.

2. **Risk concentrates around triggers.** Most clots in carriers occur with \
estrogen-containing contraception or hormone therapy, pregnancy and the weeks \
after delivery, major surgery, or prolonged immobility. These are the situations \
worth discussing with a clinician.

3. **Carriers are not treated for being carriers.** Asymptomatic carriers are \
not put on blood thinners, and routine population screening is not recommended.

4. **A negative result does not rule out a clotting disorder.** Many other \
inherited and acquired factors influence clotting risk and are not tested here.

5. **Family relevance.** A positive result may be relevant for blood relatives, \
particularly before pregnancy or starting hormonal therapy.

Discuss results with your physician or a genetic counselor; confirm in a \
CLIA/accredited laboratory before any medical decision.

**Resources:**
- National Blood Clot Alliance: https://www.stoptheclot.org/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Alpha-1 antitrypsin deficiency (SERPINA1) module ────────────────────────

ALPHA1_DISCLAIMER_TITLE = "About Alpha-1 Antitrypsin Deficiency Results"

ALPHA1_DISCLAIMER_TEXT = """\
This section reports the SERPINA1 Pi*Z (rs28929474) and Pi*S (rs17580) variants \
that cause alpha-1 antitrypsin deficiency (AATD) — an under-diagnosed, \
*actionable* condition affecting the lungs and liver.

**Please understand the following before reviewing:**

1. **The severe genotype is actionable.** PiZZ (two Z alleles) gives severe \
deficiency and raises the risk of early-onset emphysema/COPD and liver disease. \
The single most important step is avoiding smoking and vaping; augmentation \
therapy and liver monitoring may be options to discuss with a clinician.

2. **Genotype is not diagnosis.** Many carriers — and even some with deficiency \
genotypes — remain healthy, particularly non-smokers. Serum AAT level and Pi \
phenotyping confirm and quantify deficiency.

3. **A negative result does not exclude rare null alleles.** The array tests only \
Z and S. A result other than PiZZ or PiSZ does not exclude AATD — rare null and \
other deficiency alleles (e.g. Pi*null, Pi*Mmalton) are not interrogated.

4. **Family relevance.** Carrier (PiMZ/PiMS) results can be relevant for family \
planning.

5. **Professional guidance.** Discuss results with your physician or a genetic \
counselor; confirm in a CLIA/accredited laboratory before any medical action.

**Resources:**
- Alpha-1 Foundation: https://www.alpha1.org/
- COPD Foundation: https://www.copdfoundation.org/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""
