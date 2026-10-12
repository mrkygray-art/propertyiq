"""Tier 1 loan screening module.

A lender enters a property and a loan request; this module calculates the standard
underwriting numbers, checks them against the lender's own guidelines, stress-tests
the deal, and exports a PDF summary and an Excel workbook with live formulas.

It is a screening aid only: not an appraisal, a valuation opinion, or a credit approval.
It is separate from the portfolio analytics and can be switched off with
PIQ_SCREENING_ENABLED=false.
"""

DISCLAIMER = (
    "Screening aid only. Not an appraisal, valuation opinion, or credit approval. "
    "Results depend on the accuracy of the inputs provided."
)
