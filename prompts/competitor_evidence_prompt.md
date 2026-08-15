# Competitor lender evidence — extraction prompt

**Version:** 1.0
**Used by:** `src/syn_wallet/extract_competitor_evidence.py`
**Model:** claude-sonnet-5, temperature 0
**Input:** the `lenders_named` field of `data/finances/external_financials_normalized.csv`
(free-text extracted from each client's AFS borrowings note), one entity at a time.
**Output:** a strict JSON array, written to `data/processed/competitor_evidence.csv`.

## Why this exists

CLAUDE.md's hard rule: a gap between addressable wallet and Syn Bank's observed activity
is never treated as proof a competitor holds that business. It is only labelled
"confirmed competitor-held" when a specific lender is *actually named* in the client's own
AFS borrowings note. `lenders_named` already has that text extracted from the AFS, but it
is unstructured prose with real edge cases — this prompt turns it into a structured,
traceable table without inventing anything the source text doesn't say.

## The nuance this prompt has to get right

Some entries name a bank in a capacity that is **not** a lending relationship — e.g. as a
JSE sponsor or a profit-share partner. A naive keyword match against a bank-name list would
wrongly count these as "competitor holds this client's lending business." The source text
itself distinguishes these cases; the prompt's job is to preserve that distinction, not
collapse it.

## System prompt

```
You are extracting structured evidence of named competitor lending banks from a single
client's AFS (Annual Financial Statements) borrowings-note text. You will be given the
client's name and one block of extracted text.

Rules:
1. Only extract a bank as a CONFIRMED lender if the text explicitly identifies it as a
   lender, facility provider, or syndicate member for this client's debt/credit facilities.
2. If the text names a bank in a different capacity (JSE sponsor, transaction advisor,
   business/profit-share partner, auditor, etc.) — or explicitly says lenders are "not
   individually named" — do NOT extract that bank as a confirmed lender. You may still
   record it with is_confirmed_lender = false if useful context, but never mark it true.
3. Never infer a bank from general knowledge, prior AFS filings, or your own training data.
   Use only the text given. If the text says "Not disclosed" or provides no bank name,
   return an empty array.
4. Every extracted row must carry a source_excerpt: the exact substring of the input text
   that supports the extraction (for a human reviewer to check).
5. facility_amount_zar and utilised_amount_zar are nullable. Only fill them when the text
   states a specific amount for that bank; never estimate or split an aggregate figure
   across banks.
6. Output strict JSON only: an array of objects with keys
   bank_name, is_confirmed_lender, facility_amount_zar, utilised_amount_zar, source_excerpt.
   No prose, no markdown fences, no commentary.
```

## Worked example (anchors the nuance above — include as a few-shot pair)

**Input:** entity E07, OUTsurance Group —
> "Not individually named: the RCF note (Note 33, page 149) refers only to 'each lender
> participating in the RCF' without naming them. Rand Merchant Bank (a division of
> FirstRand Bank Limited) is listed in the Administration section (page 206) as the
> company's JSE sponsor, not as an RCF lender. FirstRand Bank Limited is separately
> disclosed as a business/profit-share partner for the Homeowners insurance line."

**Correct output:**
```json
[
  {
    "bank_name": "Rand Merchant Bank",
    "is_confirmed_lender": false,
    "facility_amount_zar": null,
    "utilised_amount_zar": null,
    "source_excerpt": "Rand Merchant Bank (a division of FirstRand Bank Limited) is listed in the Administration section (page 206) as the company's JSE sponsor, not as an RCF lender."
  },
  {
    "bank_name": "FirstRand Bank Limited",
    "is_confirmed_lender": false,
    "facility_amount_zar": null,
    "utilised_amount_zar": null,
    "source_excerpt": "FirstRand Bank Limited is separately disclosed as a business/profit-share partner for the Homeowners insurance line."
  }
]
```

Getting this wrong (marking either bank as a confirmed RCF lender) would misreport a
genuine "not individually named" client as having a confirmed competitor — exactly the
failure mode the hard rule exists to prevent.

## User message template

```
Client: {entity_name} ({entity_id})

Borrowings-note text:
"""
{lenders_named_text}
"""

Extract the JSON array per the system rules. Return [] if there is no usable bank-name
evidence in the text above.
```
