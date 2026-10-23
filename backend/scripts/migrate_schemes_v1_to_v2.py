"""One-time migration: legacy flat schemes.json (v1) -> canonical schema (v2).

Reads  app/data/legacy/schemes_v1.json  (the original 60 records, kept for provenance)
Writes app/data/schemes.json

Rules followed:
- Nothing is marked "verified": no record has been re-checked against an official portal yet.
- Content that was not re-checked is carried over unchanged and flagged "needs_review".
- Known-wrong legacy values (see notes on each corrected record) are NOT carried over.
- Farmer-relevant records are hand-authored below from the sources listed on each record.

Run from the backend/ directory:  python scripts/migrate_schemes_v1_to_v2.py
"""
import copy
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
LEGACY = os.path.join(BACKEND, "app", "data", "legacy", "schemes_v1.json")
OUT = os.path.join(BACKEND, "app", "data", "schemes.json")

MIGRATION_NOTE = "Migrated from the legacy dataset (v1); content not yet re-verified against an official source."


# ---------------------------------------------------------------- helpers
def deep_update(base, upd):
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base


def R(**kw):
    base = {
        "id": "", "name": "", "aliases": [], "status": "active", "superseded_by": None,
        "level": "central", "states": ["ALL"], "domains": [], "summary": "",
        "beneficiary": {"description": "", "occupations": [], "farmer_types": [], "land_max_ha": None,
                        "genders": ["any"], "social_categories": [], "age_min": None, "age_max": None},
        "crops": [], "activities": [],
        "eligibility": {"criteria": [], "exclusions": []},
        "benefits": {"summary": "", "items": []},
        "documents": [],
        "application": {"modes": [], "steps": [], "portal_url": ""},
        "contacts": {"helplines": []},
        "sources": [], "last_verified": None, "confidence": "needs_review", "notes": "",
    }
    return deep_update(base, kw)


def D(name, mandatory=None, notes=""):
    return {"name": name, "mandatory": mandatory, "notes": notes}


def H(number, label=""):
    return {"number": number, "label": label}


def S(title, url, type_):
    return {"title": title, "url": url, "type": type_}


# ------------------------------------------------- hand-authored farmer records
def farmer_records():
    recs = []

    recs.append(R(
        id="pm-kisan", name="PM-KISAN",
        aliases=["PM Kisan", "PMKISAN", "PM-KISAN Samman Nidhi", "Pradhan Mantri Kisan Samman Nidhi",
                 "Kisan Samman Nidhi", "PM Kisan Yojana", "पीएम किसान", "पीएम-किसान", "किसान सम्मान निधि"],
        domains=["income_support"],
        summary="Central sector scheme (Pradhan Mantri Kisan Samman Nidhi) giving income support to landholding "
                "farmer families to help meet agriculture-related expenses.",
        beneficiary={"description": "Landholding farmer families", "occupations": ["farmer"],
                     "farmer_types": ["landowner", "small", "marginal", "large"]},
        crops=["ALL"], activities=["crop_cultivation"],
        eligibility={
            "criteria": [
                "Landholding farmer families owning cultivable land, as recorded in state/UT land records.",
                "Bank account and mobile number must be linked to Aadhaar; eKYC is required to keep receiving instalments.",
            ],
            "exclusions": [
                "Institutional landholders.",
                "Former and present holders of constitutional posts.",
                "Serving or retired government employees/officers (multi-tasking staff and Class IV/Group D are generally excepted).",
                "Retired pensioners drawing Rs. 10,000 or more per month (multi-tasking staff/Class IV/Group D excepted).",
                "Income-tax payers.",
                "Practising professionals such as doctors, engineers, lawyers, chartered accountants and architects.",
            ]},
        benefits={"summary": "Rs. 6,000 per year in three equal instalments of Rs. 2,000 each, transferred directly to the "
                             "beneficiary's bank account.",
                  "items": [{"type": "cash", "amount": 6000, "unit": "INR/year"},
                            {"type": "cash", "amount": 2000, "unit": "INR/instalment", "note": "Three instalments a year"}]},
        documents=[D("Aadhaar", True, "Used for eKYC; the bank account must be seeded with Aadhaar."),
                   D("Bank account details (account number and IFSC)", True, "Aadhaar-seeded account."),
                   D("Land records (khasra/khatauni or equivalent as per state)", True, "Must show cultivable land in the farmer's name."),
                   D("Mobile number linked to Aadhaar", True)],
        application={"modes": ["online", "csc", "offline"],
                     "steps": ["Visit pmkisan.gov.in, open Farmers Corner and choose New Farmer Registration (or approach a Common Service Centre or the local revenue/nodal official).",
                               "Enter Aadhaar, personal, bank and land details.",
                               "The state verifies the land records and approves the beneficiary.",
                               "Complete eKYC (OTP, biometric or face authentication) on the portal/app to keep receiving instalments."],
                     "portal_url": "https://pmkisan.gov.in/"},
        contacts={"helplines": [H("155261", "PM-KISAN helpline"), H("1800-115-526", "PM-KISAN toll-free"), H("011-24300606", "PM-KISAN helpline")]},
        sources=[S("PM-KISAN official portal", "https://pmkisan.gov.in/", "official"),
                 S("Vikaspedia: PM-KISAN guide", "https://en.vikaspedia.in/viewcontent/agriculture/policies-and-schemes/crops-related/comprehensive-guide-to-the-pradhan-mantri-kisan-samman-nidhi-(pm-kisan)-scheme?lgn=en", "secondary"),
                 S("The Better India: PM-KISAN registration guide", "https://thebetterindia.com/452636/pm-kisan-20th-installment-ekyc-registration-guide", "secondary")],
        notes="Corrected from legacy: removed 'Domicile Certificate' (not a documented requirement) and the duplicate "
              "'Land Ownership Documents'/'Khasra/Khatauni' entries; added Aadhaar-linked mobile and eKYC; added toll-free helpline; "
              "added pensioner exclusion. The full exclusion list should be checked against the official guidelines.",
    ))

    recs.append(R(
        id="pmfby", name="Pradhan Mantri Fasal Bima Yojana (PMFBY)",
        aliases=["PMFBY", "PM Fasal Bima Yojana", "PM Fasal Bima", "Fasal Bima Yojana", "Pradhan Mantri Fasal Bima Yojana",
                 "प्रधानमंत्री फसल बीमा योजना", "फसल बीमा योजना"],
        domains=["crop_insurance"],
        summary="Crop insurance scheme giving financial support to farmers who suffer crop loss or damage from natural "
                "calamities, pests and diseases, at a low farmer-paid premium.",
        beneficiary={"description": "Farmers, including sharecroppers and tenant farmers, growing notified crops in notified areas",
                     "occupations": ["farmer"],
                     "farmer_types": ["landowner", "tenant", "sharecropper", "small", "marginal", "large"]},
        crops=["NOTIFIED_CROPS"], activities=["crop_cultivation", "horticulture"],
        eligibility={"criteria": [
            "Farmers, including sharecroppers and tenant farmers, growing crops notified for insurance in a notified area for the season.",
            "Which crops and areas are covered is decided by the state government's notification for each season; a crop that is not notified for your area cannot be insured under PMFBY that season.",
            "Enrolment is voluntary for all farmers, including farmers with crop loans (loanee farmers can opt out before the cut-off date).",
            "Tenant farmers and sharecroppers need a tenancy or sharecropping agreement/certificate as required by the state.",
        ], "exclusions": []},
        benefits={"summary": "Farmer-paid premium of 2% of sum insured for Kharif, 1.5% for Rabi and 5% for annual commercial/horticultural "
                             "crops; the balance premium is paid by government. Covers losses from pre-sowing to post-harvest, with add-on "
                             "cover for localized calamities, mid-season adversity and prevented sowing.",
                  "items": [{"type": "insurance_premium", "amount": 2, "unit": "% of sum insured", "note": "Kharif season"},
                            {"type": "insurance_premium", "amount": 1.5, "unit": "% of sum insured", "note": "Rabi season"},
                            {"type": "insurance_premium", "amount": 5, "unit": "% of sum insured", "note": "Annual commercial and horticultural crops"}]},
        documents=[D("Land records (RoR/khasra/khatauni/pahani or patta, as per state)", True),
                   D("Bank account details (passbook)", True),
                   D("Aadhaar", True),
                   D("Sowing certificate or crop sowing declaration", None, "Mainly used for enrolment of non-loanee farmers; requirement varies by state."),
                   D("For tenant/sharecropper: tenancy or sharecropping agreement/certificate", None, "As required by the state.")],
        application={"modes": ["online", "bank", "csc", "insurance_company"],
                     "steps": ["Loanee farmers: enrolment is normally done through the lending bank; a farmer who does not want cover must opt out in writing before the cut-off date.",
                               "Non-loanee farmers: apply through a bank branch, a Common Service Centre, an authorised insurance channel partner, or yourself on pmfby.gov.in (Farmer Corner > Apply for Crop Insurance).",
                               "Online: enter state, district, season, year, scheme and crop, upload documents, preview and submit.",
                               "Enrol before the notified last date for the season; dates vary by state and crop."],
                     "portal_url": "https://pmfby.gov.in/"},
        contacts={"helplines": [H("14447", "Krishi Rakshak Portal and Helpline (toll-free)")]},
        sources=[S("PMFBY National Crop Insurance Portal", "https://pmfby.gov.in/", "official"),
                 S("SBI General Insurance: revamped PMFBY (voluntary for all farmers)", "https://content.sbigeneral.in/uploads/4f88da72764049dca9e9543225cb7978.pdf", "secondary"),
                 S("Universal Sompo: PMFBY", "https://universalsompo.com/pm-fasal-bima-yojna/rop-covered", "secondary")],
        notes="Corrected from legacy: enrolment is voluntary for all farmers since Kharif 2020 (legacy said compulsory for loanee farmers); "
              "helpline corrected to 14447 (legacy listed the Kisan Call Centre number). Claims: report crop loss within 72 hours via 14447, "
              "the Crop Insurance app, the bank, a CSC or the local agriculture office. Crop eligibility depends on state/season notification, "
              "so crops are recorded as NOTIFIED_CROPS rather than a fixed list.",
    ))

    recs.append(R(
        id="kisan-credit-card", name="Kisan Credit Card (KCC)",
        aliases=["KCC", "Kisan Credit Card", "Kisan Credit Card Scheme", "किसान क्रेडिट कार्ड"],
        domains=["agri_credit"],
        summary="Credit facility giving farmers timely short-term credit for crop production, post-harvest needs and allied "
                "activities. Concessional rate: 7% a year on short-term crop loans up to Rs. 3 lakh; with the 3% prompt-repayment "
                "incentive the effective rate is 4% a year. Budget 2025-26 announced raising the KCC limit from Rs. 3 lakh to Rs. 5 lakh.",
        beneficiary={"description": "Farmers (owner-cultivators, tenants, oral lessees, sharecroppers), SHGs/JLGs of farmers, fishermen, dairy and poultry farmers",
                     "occupations": ["farmer", "fisherman", "livestock_farmer", "dairy_farmer", "poultry_farmer"],
                     "farmer_types": ["landowner", "tenant", "sharecropper", "small", "marginal", "large"]},
        crops=["ALL"], activities=["crop_cultivation", "fisheries", "dairy", "livestock", "poultry"],
        eligibility={"criteria": [
            "All farmers (individual or joint) who are owner cultivators, tenant farmers, oral lessees or sharecroppers.",
            "Self Help Groups or Joint Liability Groups of farmers.",
            "Also extended to fisheries and animal husbandry (including dairy and poultry) activities.",
        ], "exclusions": []},
        benefits={"summary": "Credit limit based on landholding, cropping pattern and scale of finance. Loan limit: Rs. 3 lakh, with a higher Rs. 5 lakh limit "
                             "announced in Budget 2025-26. Interest: 7% a year on short-term crop loans up to Rs. 3 lakh; the Government pays a 1.5% interest "
                             "subvention to lenders (MISS, FY 2025-26); farmers who repay on time get an additional 3% prompt-repayment incentive, "
                             "bringing the effective rate to 4%.",
                  "items": [
                      {"type": "loan_limit", "amount": 500000, "unit": "INR", "note": "Raised from Rs. 3 lakh (Budget 2025-26); the higher limit's applicability depends on the lender and borrower profile."},
                      {"type": "collateral_free_limit", "amount": 200000, "unit": "INR", "note": "Reported as Rs. 2 lakh per borrower (earlier Rs. 1.6 lakh); confirm with the bank."},
                      {"type": "interest_rate", "amount": 7, "unit": "% per year", "note": "Concessional rate on short-term crop loans up to Rs. 3 lakh."},
                      {"type": "interest_subvention", "amount": 1.5, "unit": "% per year", "note": "Paid by the Government to lending institutions under MISS (FY 2025-26)."},
                      {"type": "prompt_repayment_incentive", "amount": 3, "unit": "% per year", "note": "For timely repayment; effective rate becomes 4%."},
                      {"type": "personal_accident_insurance", "amount": 50000, "unit": "INR", "note": "Carried over from the legacy dataset; not re-verified."}]},
        documents=[D("Land ownership documents or lease agreement", True),
                   D("Aadhaar", True),
                   D("PAN card", False, "Needed for loans above Rs. 1 lakh."),
                   D("Passport size photographs", None),
                   D("Application form", True),
                   D("For allied activities: registration/licence as applicable", None)],
        application={"modes": ["bank"],
                     "steps": ["Apply at a bank branch (commercial bank, regional rural bank or cooperative bank) with the required documents."],
                     "portal_url": ""},
        contacts={"helplines": [H("1800-180-1551", "Kisan Call Centre (general farmer helpline)")]},
        sources=[S("Drishti IAS: Kisan Credit Card Scheme (14 Mar 2026)", "https://www.drishtiias.com/daily-updates/daily-news-analysis/kisan-credit-card-scheme", "secondary"),
                 S("Business Standard: Budget 2025-26 KCC limit", "https://www.business-standard.com/amp/industry/agriculture/budget-2025-26-higher-loan-limit-may-benefit-10-of-kcc-accounts-125020401617_1.html", "secondary")],
        notes="Corrected from legacy: loan limit, interest rate, subvention and prompt-repayment incentive are now separate items (legacy "
              "conflated them as '2% subvention, effective 4%'); removed wrong portal URL (pmkisan.gov.in). No single national KCC application "
              "portal is recorded; apply through a bank. Sources conflict on the collateral-free limit (Rs. 1.6 lakh vs Rs. 2 lakh) - needs official check.",
    ))

    recs.append(R(
        id="pm-kisan-maan-dhan", name="PM Kisan Maan Dhan Yojana",
        aliases=["PM-KMY", "PMKMY", "Kisan Maan Dhan", "PM Kisan Maandhan Yojana", "PM Kisan Maan-Dhan Yojana",
                 "Kisan Pension Yojana", "किसान मानधन योजना"],
        domains=["farmer_pension", "social_security"],
        summary="Voluntary, contributory pension scheme for small and marginal farmers: a minimum pension of Rs. 3,000 per month "
                "after age 60, with matching contribution by the government.",
        beneficiary={"description": "Small and marginal farmers aged 18-40 years", "occupations": ["farmer"],
                     "farmer_types": ["small", "marginal"], "land_max_ha": 2, "age_min": 18, "age_max": 40},
        crops=["ALL"], activities=[],
        eligibility={"criteria": ["Small and marginal farmers aged 18-40 years with cultivable land up to 2 hectares as per land records."],
                     "exclusions": ["Farmers covered under PM Shram Yogi Maandhan, PM Laghu Vyapari Maandhan, NPS or ESIC."]},
        benefits={"summary": "Guaranteed minimum pension of Rs. 3,000 per month after age 60. Equal pension to the spouse after the subscriber's "
                             "death. If the subscriber dies before 60, the spouse can continue the contribution or exit with the subscriber's "
                             "contribution plus interest. Government contributes an equal amount.",
                  "items": [{"type": "pension", "amount": 3000, "unit": "INR/month", "note": "From age 60"}]},
        documents=[D("Aadhaar", True), D("Land records showing holding up to 2 hectares", True),
                   D("Bank account/savings account details", True), D("Mobile number", None), D("Age proof", None)],
        application={"modes": [], "steps": [], "portal_url": "https://maandhan.in/"},
        contacts={"helplines": [H("1800-267-6888", "Maandhan helpline")]},
        sources=[S("Maandhan portal (carried over from legacy dataset)", "https://maandhan.in/", "unverified")],
        notes="Carried over from the legacy dataset with farmer metadata added; application steps and monthly contribution amounts (which depend on "
              "entry age) are not recorded because they have not been sourced.",
    ))

    # ---- superseded / repealed records and their replacements
    recs.append(R(
        id="rythu-bandhu", name="Rythu Bandhu (Telangana)", level="state", states=["Telangana"],
        aliases=["Rythu Bandhu", "Rythu Bandhu Telangana", "రైతు బంధు", "रायथु बंधु"],
        status="superseded", superseded_by="rythu-bharosa", domains=["income_support"],
        summary="Telangana's earlier investment-support scheme for farmers (Rs. 5,000 per acre per season, Rs. 10,000 per acre per year). "
                "Replaced by Rythu Bharosa from 26 January 2025.",
        beneficiary={"description": "Farmers in Telangana", "occupations": ["farmer"], "farmer_types": ["landowner"]},
        crops=["ALL"], activities=["crop_cultivation"],
        eligibility={"criteria": ["All farmers (cultivators) with agricultural land ownership in Telangana as per land records, including pattadars of assigned lands; no ceiling on landholding."], "exclusions": []},
        benefits={"summary": "Historical: Rs. 5,000 per acre per season (Rs. 10,000 per acre per year). See Rythu Bharosa (Rs. 12,000 per acre per year) for current support.",
                  "items": [{"type": "cash", "amount": 10000, "unit": "INR/acre/year", "note": "Historical amount"}]},
        documents=[D("Aadhaar"), D("Land pattadar passbook"), D("Bank account details"), D("Mobile number")],
        application={"modes": [], "steps": [], "portal_url": ""},
        sources=[S("Deccan Chronicle: Rythu Bharosa guidelines replace Rythu Bandhu", "https://deccanchronicle.com/southern-states/telangana/telangana-govt-issues-guidelines-for-rythu-bharosa-1853665", "secondary"),
                 S("Siasat: Telangana issues guidelines for Rythu Bharosa", "https://www.siasat.com/telangana-government-issues-guidelines-for-rythu-bharosa-3163536/amp/", "secondary")],
        notes="Superseded: legacy listed this as the current Telangana farmer-support scheme. Kept so questions about the old name can be redirected to Rythu Bharosa.",
    ))

    recs.append(R(
        id="rythu-bharosa", name="Rythu Bharosa (Telangana)", level="state", states=["Telangana"],
        aliases=["Rythu Bharosa", "Rythu Bharosa Telangana", "రైతు భరోసా", "रायथु भरोसा"],
        domains=["income_support"],
        summary="Telangana's crop-investment support scheme for farmers, launched on 26 January 2025 as the successor to Rythu Bandhu.",
        beneficiary={"description": "Farmers in Telangana holding cultivable agricultural land", "occupations": ["farmer"], "farmer_types": ["landowner"]},
        crops=["ALL"], activities=["crop_cultivation"],
        eligibility={"criteria": [
            "Farmers (pattadars, including pattadars of forest-rights podu land) holding active cultivable agricultural land in Telangana.",
            "Assistance is limited to active cultivable land; it is based on the land extent."],
            "exclusions": ["Non-cultivable land, such as land converted for real estate, infrastructure or commercial use, is excluded."]},
        benefits={"summary": "Rs. 6,000 per acre per crop season (Rs. 12,000 per acre per year across Kharif and Rabi), transferred directly to the bank account.",
                  "items": [{"type": "cash", "amount": 6000, "unit": "INR/acre/season"}, {"type": "cash", "amount": 12000, "unit": "INR/acre/year"}]},
        documents=[D("Land pattadar passbook / land records", None, "Carried over from the predecessor scheme Rythu Bandhu; the official Rythu Bharosa list is not yet sourced - confirm."),
                   D("Bank account details", None, "Carried over from Rythu Bandhu (payment is by direct bank transfer); confirm."),
                   D("Aadhaar", None, "Carried over from Rythu Bandhu; confirm.")],
        application={"modes": [], "steps": [], "portal_url": ""},
        sources=[S("Deccan Chronicle: Telangana issues guidelines for Rythu Bharosa", "https://deccanchronicle.com/southern-states/telangana/telangana-govt-issues-guidelines-for-rythu-bharosa-1853665", "secondary"),
                 S("RTI Wiki: Rythu Bandhu to Rythu Bharosa", "https://righttoinformation.wiki/_export/xhtml/yojana/rythu-bandhu-telangana", "secondary")],
        notes="The document list is carried over from Rythu Bandhu and flagged as unconfirmed; application steps and the official portal URL are not yet recorded (not sourced). One news report mentions an "
              "'Indiramma Atmiya Bharosa' sub-scheme for landless agricultural families (Rs. 12,000 per year, identified via MGNREGS job-card records); "
              "not recorded until verified. Eligible Telangana farmers can receive both Rythu Bharosa and PM-KISAN.",
    ))

    recs.append(R(
        id="kalia-yojana", name="KALIA Yojana (Odisha)", level="state", states=["Odisha"],
        aliases=["KALIA", "Kalia Yojana", "KALIA Scheme", "Krushak Assistance for Livelihood and Income Augmentation"],
        status="superseded", superseded_by="cm-kisan-odisha", domains=["income_support"],
        summary="Odisha's earlier farmer livelihood and income-support scheme (Krushak Assistance for Livelihood and Income Augmentation). "
                "Replaced by CM-KISAN in 2024.",
        beneficiary={"description": "Farmers and landless agricultural households in Odisha", "occupations": ["farmer"],
                     "farmer_types": ["small", "marginal", "landless"]},
        crops=["ALL"], activities=["crop_cultivation"],
        eligibility={"criteria": ["Small and marginal farmers with cultivable land up to 5 acres, landless agricultural households and vulnerable agricultural households resident in Odisha."], "exclusions": []},
        benefits={"summary": "Historical scheme. Benefit amounts are not recorded in this knowledge base (legacy figures could not be confirmed); see CM-KISAN for current support in Odisha.",
                  "items": []},
        documents=[D("Aadhaar"), D("Land records (for cultivators)"), D("Bank account details"), D("Ration card"),
                   D("Caste certificate (if applicable)", False), D("Disability certificate (if applicable)", False), D("Age proof")],
        application={"modes": [], "steps": [], "portal_url": ""},
        sources=[S("Deccan Chronicle: Odisha replaces KALIA with CM-Kisan Yojana", "https://deccanchronicle.com/nation/odisha-replaces-kalia-with-cm-kisan-yojana-for-farmers-1819732", "secondary"),
                 S("Odisha Plus: CMKY replaces KALIA", "https://odisha.plus/2024/08/cmky-replaces-kalia-odisha-governments-flagship-farmer-help-programme/", "secondary")],
        notes="Superseded. The legacy benefit text (Rs. 4,000 per season / Rs. 8,000 a year, insurance cover) was dropped because it does not match "
              "the current CM-KISAN figures and could not be confirmed for KALIA.",
    ))

    recs.append(R(
        id="cm-kisan-odisha", name="CM-KISAN Yojana (Odisha)", level="state", states=["Odisha"],
        aliases=["CM-KISAN", "CM Kisan", "CM Kisan Yojana", "CMKY", "Mukhyamantri Kisan Yojana Odisha", "CM-KISAN Odisha"],
        domains=["income_support"],
        summary="Odisha government's income-support scheme for small and marginal farmers and landless agricultural households, "
                "launched in 2024 to replace KALIA.",
        beneficiary={"description": "Small and marginal farmers and landless agricultural households in Odisha", "occupations": ["farmer"],
                     "farmer_types": ["small", "marginal", "landless"]},
        crops=["ALL"], activities=["crop_cultivation"],
        eligibility={"criteria": [
            "Permanent resident of Odisha (Odisha domicile/residence certificate).",
            "Small and marginal farmers, or landless agricultural households.",
            "Land details should be registered on the Krushak Odisha portal before applying."], "exclusions": []},
        benefits={"summary": "Small and marginal farmers: Rs. 4,000 per year in two equal instalments of Rs. 2,000 (around Nuakhai and Akshaya Tritiya). "
                             "Landless agricultural households: Rs. 12,500 in three instalments as livelihood support. Reported to be in addition to PM-KISAN.",
                  "items": [{"type": "cash", "amount": 4000, "unit": "INR/year", "note": "Small and marginal farmers, two instalments of Rs. 2,000"},
                            {"type": "cash", "amount": 12500, "unit": "INR", "note": "Landless agricultural households, three instalments"}]},
        documents=[D("Aadhaar", None, "Aadhaar-based eKYC is part of the online process."), D("Odisha domicile/residence certificate", None)],
        application={"modes": ["online"],
                     "steps": ["Apply online through the CM-KISAN portal.", "Register land details on the Krushak Odisha portal first.", "Complete Aadhaar-based eKYC and note the token number."],
                     "portal_url": ""},
        contacts={"helplines": [H("155333", "Krushi Samruddhi helpline")]},
        sources=[S("Vikaspedia: CM Kisan Yojana", "https://en.vikaspedia.in/viewcontent/schemesall/state-specific-schemes/welfare-schemes-of-odisha/cm-kisan-yojana", "secondary"),
                 S("Deccan Chronicle: Odisha releases CM-Kisan funds", "https://deccanchronicle.com/amp/nation/odisha-govt-releases-cm-kisan-yojana-funds-1822110", "secondary")],
        notes="Official portal URL not recorded: one source names cmkisan.odisha.gov.in (and cmkportal.odisha.gov.in) but this is unconfirmed. "
              "Sources differ on whether the landless Rs. 12,500 is a one-time or yearly amount.",
    ))

    recs.append(R(
        id="rythu-bima", name="Rythu Bima (Telangana)", level="state", states=["Telangana"],
        aliases=["Rythu Bima", "Rythu Bima Telangana", "రైతు బీమా"],
        domains=["life_insurance", "social_security"],
        summary="Life insurance scheme for farmers in Telangana providing a life cover to protect farmer families in case of the farmer's death.",
        beneficiary={"description": "Farmers in Telangana aged 18-59", "occupations": ["farmer"], "farmer_types": ["landowner"], "age_min": 18, "age_max": 59},
        crops=["ALL"], activities=[],
        eligibility={"criteria": ["All farmers aged 18-59 years in Telangana. Premium is paid entirely by the state government."], "exclusions": []},
        benefits={"summary": "Life insurance cover of Rs. 5 lakh in case of death (natural or accidental). Premium paid by the government. Claims are settled quickly after application.",
                  "items": [{"type": "life_cover", "amount": 500000, "unit": "INR"}]},
        documents=[D("Aadhaar"), D("Bank account details"), D("Age proof"), D("Land documents"), D("Rythu Bandhu enrollment proof", None, "Legacy requirement; Rythu Bandhu has since been replaced - confirm current requirement.")],
        application={"modes": [], "steps": [], "portal_url": ""},
        sources=[S("Legacy dataset (not re-verified)", "", "unverified")],
        notes="Carried over from the legacy dataset. The legacy text tied eligibility to enrolment in Rythu Bandhu, which has been replaced by Rythu Bharosa; "
              "the current linkage and premium figure were not re-verified and the legacy premium amount was dropped.",
    ))

    recs.append(R(
        id="mgnrega", name="MGNREGA (Mahatma Gandhi National Rural Employment Guarantee Act)",
        aliases=["MGNREGA", "MGNREGS", "NREGA", "NREGS", "Mahatma Gandhi NREGA", "Mahatma Gandhi National Rural Employment Guarantee Act", "100 days work scheme", "मनरेगा"],
        status="repealed", superseded_by="vb-g-ram-g", domains=["employment"],
        summary="Statutory rural wage-employment guarantee (100 days per household per year) enacted in 2005. Repealed with effect from 1 July 2026 "
                "and replaced by the VB-G RAM G Act, 2025, which guarantees 125 days.",
        beneficiary={"description": "Rural households willing to do unskilled manual work"},
        crops=[], activities=[],
        eligibility={"criteria": ["Adult members of rural households willing to do unskilled manual work; a Job Card was required."], "exclusions": []},
        benefits={"summary": "Historical: guaranteed 100 days of unskilled wage employment per rural household per year. Existing e-KYC-verified job cards remain valid until new Gramin Rozgar Guarantee Cards are issued under VB-G RAM G.",
                  "items": [{"type": "employment_days", "amount": 100, "unit": "days/household/year", "note": "Historical"}]},
        documents=[D("Aadhaar"), D("Address proof"), D("Job card (if already enrolled)", False), D("Bank/post office account"), D("Passport size photographs")],
        application={"modes": [], "steps": [], "portal_url": ""},
        sources=[S("Press release (16 June 2026): VB-G RAM G Act operationalised, MGNREGA repealed from 1 July 2026 (copy hosted by Meghalaya DIPR)", "https://meghalaya.gov.in/sites/default/files/press_release/Press_Release_DIPR_16th_June_2026_English.pdf", "press_release"),
                 S("The Statesman: Centre announces July 2026 rollout of VB-G RAM G", "https://www.thestatesman.com/business/centre-announces-july-2026-rollout-of-viksit-bharat-g-ram-g-act-replacing-mgnrega-1503592596.html", "secondary")],
        notes="Repealed: legacy listed this as an active scheme with wage rates of Rs. 213-374 (outdated; dropped). The repeal date and replacement were "
              "checked against a central government press release; the eligibility and document lists are legacy content.",
    ))

    recs.append(R(
        id="vb-g-ram-g", name="Viksit Bharat - Guarantee for Rozgar and Ajeevika Mission (Gramin) (VB-G RAM G)",
        aliases=["VB-G RAM G", "VB G RAM G", "VB-GRAM", "VB-G RAM G Act", "Viksit Bharat G RAM G", "G RAM G", "VBGRAMG"],
        domains=["employment", "livelihoods"],
        summary="Rural employment guarantee law in force from 1 July 2026, replacing MGNREGA. Raises the statutory guarantee from 100 to 125 days "
                "of wage employment and introduces Viksit Gram Panchayat Plans and new Gramin Rozgar Guarantee Cards.",
        beneficiary={"description": "Rural households whose adult members are willing to do unskilled manual work"},
        crops=[], activities=[],
        eligibility={"criteria": ["Rural households whose adult members are willing to do unskilled manual work. Detailed rules are being finalised by the Ministry of Rural Development - confirm current rules."], "exclusions": []},
        benefits={"summary": "Statutory guarantee of 125 days of wage employment per rural household per year. Wages are paid through direct benefit transfer, weekly or within 15 days of closing the muster roll. States share the cost, unlike MGNREGA which was fully centre-funded.",
                  "items": [{"type": "employment_days", "amount": 125, "unit": "days/household/year"}]},
        documents=[D("Gramin Rozgar Guarantee Card", None, "Existing e-KYC-verified MGNREGA job cards remain valid until new cards are issued.")],
        application={"modes": [], "steps": [], "portal_url": ""},
        sources=[S("Press release (16 June 2026): VB-G RAM G Act operationalised from 1 July 2026 (copy hosted by Meghalaya DIPR)", "https://meghalaya.gov.in/sites/default/files/press_release/Press_Release_DIPR_16th_June_2026_English.pdf", "press_release"),
                 S("UNI: Centre announces rollout of VB-G RAM G from July 1", "https://test.uniindia.com/centre-announces-rollout-of-viksit-bharat-g-ram-g-act-from-july-1-replacing-mgnrega/india/news/3839335.html", "secondary")],
        notes="Application process, portal and helpline are not yet recorded; the rules were still being finalised at the time of the sources.",
    ))

    # ---- state farmer schemes (new)
    recs.append(R(
        id="annadata-sukhibhava", name="Annadata Sukhibhava (Andhra Pradesh)", level="state", states=["Andhra Pradesh"],
        aliases=["Annadata Sukhibhava", "Annadatha Sukhibhava", "Annadata Sukheebhava", "Annadata Sukhibhava PM Kisan", "అన్నదాత సుఖీభవ"],
        domains=["income_support"],
        summary="Andhra Pradesh's farmer income-support scheme, launched on 2 August 2025: Rs. 20,000 per year to eligible farmer families, "
                "combining Rs. 6,000 from PM-KISAN with a Rs. 14,000 state top-up.",
        beneficiary={"description": "Small, marginal and tenant farmer families in Andhra Pradesh", "occupations": ["farmer"],
                     "farmer_types": ["small", "marginal", "tenant"]},
        crops=["ALL"], activities=["crop_cultivation"],
        eligibility={"criteria": [
            "Farmer families in Andhra Pradesh; small, marginal and tenant farmers are covered.",
            "Landowning farmers should be PM-KISAN beneficiaries; only one farmer per family receives assistance.",
            "Farmers must complete e-KYC and other requirements (contact the nearest Rythu Seva Kendra)."], "exclusions": []},
        benefits={"summary": "Rs. 20,000 per year in three instalments: Rs. 6,000 from PM-KISAN plus Rs. 14,000 from the state. The first instalment in August 2025 was Rs. 7,000 (Rs. 5,000 state plus Rs. 2,000 PM-KISAN).",
                  "items": [{"type": "cash", "amount": 20000, "unit": "INR/year", "note": "Rs. 14,000 state top-up plus Rs. 6,000 PM-KISAN"},
                            {"type": "cash", "amount": 14000, "unit": "INR/year", "note": "State share"}]},
        documents=[D("PM-KISAN registration number", None, "For PM-KISAN beneficiaries.")],
        application={"modes": ["agriculture_department"],
                     "steps": ["Check status on the Annadata Sukhibhava portal, the Mana Mitra WhatsApp governance app or the integrated call centre.",
                               "Complete e-KYC and other requirements at the nearest Rythu Seva Kendra."],
                     "portal_url": ""},
        sources=[S("Vikaspedia: Annadata Sukhibhava", "https://en.vikaspedia.in/viewcontent/schemesall/state-specific-schemes/welfare-schemes-of-andhra-pradesh/annadata-sukhibhava-scheme", "secondary"),
                 S("PTI via The Week: Andhra CM to launch Annadatha Sukhibhava on Aug 2", "https://www.theweek.in/wire-updates/national/2025/07/31/mds19-ap-scheme-cm.html", "secondary"),
                 S("The South First: Annadata Sukheebhava rollout", "https://thesouthfirst.com/andhrapradesh/andhra-pradesh-set-to-roll-out-annadata-sukheebhava-scheme-to-help-farmers-in-their-kharif-crop/", "secondary")],
        notes="Official portal URL not recorded (one source names annadathasukhibhava.ap.gov.in; unconfirmed). Reports differ on how tenant farmers, "
              "who are outside PM-KISAN, are paid; confirm with the Agriculture Department. The legacy AP records (YSR Cheyutha, YSR Pension Kanuka) are not farmer schemes.",
    ))

    recs.append(R(
        id="namo-shetkari-mahasanman-nidhi", name="Namo Shetkari Mahasanman Nidhi Yojana (Maharashtra)", level="state", states=["Maharashtra"],
        aliases=["Namo Shetkari", "Namo Shetkari Yojana", "Namo Shetkari Maha Samman Nidhi", "Namo Shetkari Mahasanman Nidhi",
                 "NSMNY", "नमो शेतकरी महासन्मान निधी"],
        domains=["income_support"],
        summary="Maharashtra's income-support scheme giving eligible farmer families an extra Rs. 6,000 per year on top of PM-KISAN, "
                "so that an eligible family receives Rs. 12,000 a year.",
        beneficiary={"description": "Maharashtra farmer families eligible under PM-KISAN", "occupations": ["farmer"], "farmer_types": ["landowner"]},
        crops=["ALL"], activities=["crop_cultivation"],
        eligibility={"criteria": [
            "Farmer families of Maharashtra who are eligible beneficiaries of PM-KISAN.",
            "Aadhaar-linked bank account and PM-KISAN registration are required.",
            "The beneficiary list is drawn from the PM-KISAN database; the PM-KISAN exclusions apply."], "exclusions": []},
        benefits={"summary": "Rs. 6,000 per year in three instalments of Rs. 2,000 each, paid by the state in addition to Rs. 6,000 under PM-KISAN (Rs. 12,000 a year in total).",
                  "items": [{"type": "cash", "amount": 6000, "unit": "INR/year"}, {"type": "cash", "amount": 2000, "unit": "INR/instalment", "note": "Three instalments a year"}]},
        documents=[D("PM-KISAN registration number", None), D("Aadhaar", None), D("Mobile number", None)],
        application={"modes": [], "steps": ["Farmers registered and eligible under PM-KISAN are reported to be covered automatically; no separate application is described in the sources. Contact the District Agriculture Officer for help."], "portal_url": ""},
        sources=[S("Vikaspedia: Namo Shetkari Maha Samman Nidhi Yojana", "https://en.vikaspedia.in/viewcontent/schemesall/state-specific-schemes/welfare-schemes-of-maharashtra/namo-shetkari-maha-samman-nidhi-yojana", "secondary"),
                 S("Deccan Herald: Maharashtra Budget 2023-24", "https://www.deccanherald.com/amp/story/india%2Fmaharashtra-budget-farmers-to-get-rs-6000-per-year-as-shinde-govt-eyes-political-harvest-1198590.html", "secondary"),
                 S("Public Services Map: Namo Shetkari Mahasanman Nidhi Yojana", "https://www.publicservicesmap.in/schemes/namo-shetkari-mahasanman-nidhi-yojana", "secondary")],
        notes="Official portal URL not recorded. Not crop-specific: it applies to all PM-KISAN-eligible Maharashtra farmers, including cotton farmers.",
    ))
    return recs


# --------------------------------------------- mechanical conversion of legacy rows
# legacy name -> overrides for the non-farmer records
NF = {
    "Ayushman Bharat - PMJAY": dict(id="ayushman-bharat-pmjay", aliases=["PMJAY", "Ayushman Bharat", "Ayushman Bharat PMJAY", "Ayushman Bharat Yojana"], domains=["health_insurance"]),
    "PM Awas Yojana - Gramin (PMAY-G)": dict(id="pmay-gramin", aliases=["PMAY-G", "PMAYG", "PM Awas Yojana Gramin", "PM Awas Gramin", "Pradhan Mantri Awas Yojana Gramin"], domains=["housing"]),
    "PM Awas Yojana - Urban (PMAY-U)": dict(id="pmay-urban", aliases=["PMAY-U", "PMAYU", "PM Awas Yojana Urban", "PM Awas Urban", "Pradhan Mantri Awas Yojana Urban"], domains=["housing"]),
    "Mudra Loan (PMMY)": dict(id="mudra-loan-pmmy", aliases=["PMMY", "Mudra Loan", "Mudra Yojana", "Pradhan Mantri Mudra Yojana"], domains=["enterprise_credit"], occupations=["small_business"], notes="Non-farm enterprises only; not an agricultural credit scheme (see Kisan Credit Card)."),
    "PM Kaushal Vikas Yojana 4.0 (PMKVY)": dict(id="pmkvy-4", aliases=["PMKVY", "PM Kaushal Vikas Yojana", "PMKVY 4.0"], domains=["skill_development"]),
    "Ujjwala Yojana 2.0 (PMUY)": dict(id="ujjwala-yojana-pmuy", aliases=["PMUY", "Ujjwala Yojana", "PM Ujjwala Yojana", "Ujjwala 2.0", "Pradhan Mantri Ujjwala Yojana", "Ujjwala LPG"], domains=["clean_energy"], genders=["women"], notes="LPG connection scheme. Not the Ujjawala anti-trafficking scheme (see ujjawala-anti-trafficking)."),
    "Startup India": dict(id="startup-india", aliases=["Startup India"], domains=["enterprise"]),
    "Stand-Up India": dict(id="stand-up-india", aliases=["Stand Up India", "Standup India"], domains=["enterprise_credit"]),
    "Beti Bachao Beti Padhao": dict(id="beti-bachao-beti-padhao", aliases=["BBBP", "Beti Bachao Beti Padhao"], domains=["girl_child"], genders=["women"]),
    "Sukanya Samriddhi Yojana (SSY)": dict(id="sukanya-samriddhi-yojana", aliases=["SSY", "Sukanya Samriddhi", "Sukanya Samriddhi Yojana"], domains=["savings", "girl_child"], genders=["women"]),
    "PM Jeevan Jyoti Bima Yojana (PMJJBY)": dict(id="pmjjby", aliases=["PMJJBY", "PM Jeevan Jyoti Bima Yojana", "Jeevan Jyoti Bima"], domains=["life_insurance"]),
    "PM Suraksha Bima Yojana (PMSBY)": dict(id="pmsby", aliases=["PMSBY", "PM Suraksha Bima Yojana", "Suraksha Bima"], domains=["accident_insurance"]),
    "Atal Pension Yojana (APY)": dict(id="atal-pension-yojana", aliases=["APY", "Atal Pension Yojana", "Atal Pension"], domains=["pension"]),
    "National Pension System (NPS)": dict(id="national-pension-system", aliases=["NPS", "National Pension System", "National Pension Scheme"], domains=["pension"]),
    "PM Vishwakarma Yojana": dict(id="pm-vishwakarma", aliases=["PM Vishwakarma", "Vishwakarma Yojana"], domains=["artisan_support"], occupations=["artisan"]),
    "PM Garib Kalyan Anna Yojana (PMGKAY)": dict(id="pmgkay", aliases=["PMGKAY", "PM Garib Kalyan Anna Yojana", "Garib Kalyan Anna Yojana"], domains=["food_security"]),
    "Jal Jeevan Mission": dict(id="jal-jeevan-mission", aliases=["JJM", "Jal Jeevan Mission", "Har Ghar Jal"], domains=["drinking_water"]),
    "PM SVANidhi (Street Vendor)": dict(id="pm-svanidhi", aliases=["PM SVANidhi", "SVANidhi", "PM Street Vendor Atmanirbhar Nidhi"], domains=["enterprise_credit"], occupations=["street_vendor"]),
    "PM Matru Vandana Yojana (PMMVY)": dict(id="pmmvy", aliases=["PMMVY", "PM Matru Vandana Yojana", "Matru Vandana"], domains=["maternity_benefit"], genders=["women"]),
    "Poshan Abhiyaan (National Nutrition Mission)": dict(id="poshan-abhiyaan", aliases=["Poshan Abhiyaan", "Poshan Abhiyan", "National Nutrition Mission"], domains=["nutrition"]),
    "National Social Assistance Programme (NSAP)": dict(id="nsap", aliases=["NSAP", "National Social Assistance Programme"], domains=["social_pension"]),
    "Deendayal Antyodaya Yojana - NRLM": dict(id="day-nrlm", aliases=["DAY-NRLM", "NRLM", "National Rural Livelihoods Mission", "Deendayal Antyodaya Yojana NRLM", "Aajeevika"], domains=["livelihoods"]),
    "Deendayal Antyodaya Yojana - NULM": dict(id="day-nulm", aliases=["DAY-NULM", "NULM", "National Urban Livelihoods Mission"], domains=["livelihoods"]),
    "PM Shram Yogi Maandhan Yojana": dict(id="pm-sym", aliases=["PM-SYM", "PMSYM", "Shram Yogi Maandhan", "PM Shram Yogi Maan-dhan"], domains=["pension"], occupations=["unorganised_worker"]),
    "E-Shram Card (Unorganized Workers)": dict(id="e-shram", aliases=["e-Shram", "eShram", "E-Shram Card", "e Shram card"], domains=["social_security"], occupations=["unorganised_worker"]),
    "Agnipath Scheme (Agniveer)": dict(id="agnipath", aliases=["Agnipath", "Agniveer", "Agnipath Scheme"], domains=["defence_recruitment"]),
    "Ladli Behna Yojana": dict(id="ladli-behna-yojana-mp", aliases=["Ladli Behna", "Ladli Behna Yojana", "Ladli Behna Yojana Madhya Pradesh", "लाडली बहना योजना"], domains=["women_cash_transfer"], genders=["women"]),
    "Mahtari Vandana Yojana (Chhattisgarh)": dict(id="mahtari-vandana-yojana", aliases=["Mahtari Vandana", "Mahtari Vandan Yojana", "Mahtari Vandana Yojana"], domains=["women_cash_transfer"], genders=["women"]),
    "Kanya Sumangala Yojana (UP)": dict(id="kanya-sumangala-yojana", aliases=["Kanya Sumangala", "Kanya Sumangala Yojana"], domains=["girl_child"], genders=["women"]),
    "Mukhyamantri Kanya Vivah Yojana (Bihar)": dict(id="mukhyamantri-kanya-vivah-yojana-bihar", aliases=["Kanya Vivah Yojana Bihar", "Mukhyamantri Kanya Vivah Yojana"], domains=["marriage_assistance"], genders=["women"]),
    "RTE Act Free Education": dict(id="rte-free-education", aliases=["RTE", "RTE Act", "Right to Education", "Right to Education Act"], domains=["education"], notes="Application process varies by state (state education portals); no single national URL recorded."),
    "Post Matric Scholarship for SC Students": dict(id="post-matric-scholarship-sc", aliases=["Post Matric Scholarship SC"], domains=["education", "scholarship"], social_categories=["SC"]),
    "Post Matric Scholarship for ST Students": dict(id="post-matric-scholarship-st", aliases=["Post Matric Scholarship ST"], domains=["education", "scholarship"], social_categories=["ST"]),
    "Post Matric Scholarship for OBC Students": dict(id="post-matric-scholarship-obc", aliases=["Post Matric Scholarship OBC"], domains=["education", "scholarship"], social_categories=["OBC"]),
    "National Scholarship for Persons with Disabilities": dict(id="national-scholarship-pwd", aliases=["National Scholarship for PwD", "Scholarship for Persons with Disabilities"], domains=["education", "scholarship", "disability"]),
    "UDID Card (Unique Disability ID)": dict(id="udid-card", aliases=["UDID", "UDID Card", "Unique Disability ID", "Swavlamban Card"], domains=["disability"]),
    "Indira Gandhi National Widow Pension Scheme": dict(id="ignwps", aliases=["IGNWPS", "Indira Gandhi National Widow Pension Scheme", "Widow Pension Scheme"], domains=["social_pension"], genders=["women"]),
    "Indira Gandhi National Disability Pension Scheme": dict(id="igndps", aliases=["IGNDPS", "Indira Gandhi National Disability Pension Scheme"], domains=["social_pension", "disability"]),
    "Swadhar Greh Scheme": dict(id="swadhar-greh", aliases=["Swadhar Greh", "Swadhar Greh Scheme"], domains=["women_welfare"], genders=["women"]),
    "Ujjawala Scheme (Anti-Trafficking)": dict(id="ujjawala-anti-trafficking", aliases=["Ujjawala Scheme", "Ujjawala anti trafficking scheme"], domains=["women_welfare"], genders=["women"], notes="Anti-trafficking scheme. Not the Ujjwala LPG scheme (see ujjwala-yojana-pmuy)."),
    "YSR Cheyutha (Andhra Pradesh)": dict(id="ysr-cheyutha", aliases=["YSR Cheyutha", "Cheyutha"], domains=["women_cash_transfer"], genders=["women"], notes="State-government branding and benefit amount may have changed after the 2024 change of government; not yet verified."),
    "YSR Pension Kanuka (Andhra Pradesh)": dict(id="ysr-pension-kanuka", aliases=["YSR Pension Kanuka", "Pension Kanuka"], domains=["social_pension"], notes="State-government branding and pension amounts may have changed after the 2024 change of government; not yet verified."),
    "Aasara Pension (Telangana)": dict(id="aasara-pension-telangana", aliases=["Aasara Pension", "Aasara Pensions"], domains=["social_pension"]),
    "Kalyana Lakshmi (Telangana)": dict(id="kalyana-lakshmi", aliases=["Kalyana Lakshmi", "Kalyana Laxmi"], domains=["marriage_assistance"], genders=["women"]),
    "Shaadi Mubarak (Telangana)": dict(id="shaadi-mubarak", aliases=["Shaadi Mubarak"], domains=["marriage_assistance"], genders=["women"]),
    "Mukhyamantri Sukanya Yojana (Jharkhand)": dict(id="mukhyamantri-sukanya-yojana-jharkhand", aliases=["Mukhyamantri Sukanya Yojana", "Sukanya Yojana Jharkhand"], domains=["girl_child"], genders=["women"]),
    "PM CARES for Children Scheme": dict(id="pm-cares-for-children", aliases=["PM CARES for Children", "PM CARES"], domains=["child_welfare"]),
    "Nari Samman Yojana (Himachal Pradesh)": dict(id="nari-samman-yojana-himachal", aliases=["Nari Samman Yojana", "Nari Samman"], domains=["women_welfare"], genders=["women"]),
    "National Family Benefit Scheme (NFBS)": dict(id="national-family-benefit-scheme", aliases=["NFBS", "National Family Benefit Scheme"], domains=["social_assistance"]),
    "Annapurna Scheme": dict(id="annapurna-scheme", aliases=["Annapurna", "Annapurna Scheme"], domains=["food_security"]),
}

# legacy rows that are replaced by hand-authored records (or merged) and must not be mechanically converted
HAND_AUTHORED_LEGACY = {"PM-KISAN", "PM Fasal Bima Yojana (PMFBY)", "Kisan Credit Card (KCC)", "PM Kisan Maan Dhan Yojana",
                        "Kalia Yojana (Odisha)", "Rythu Bandhu (Telangana)", "Rythu Bima (Telangana)",
                        "MGNREGA (Mahatma Gandhi NREGA)"}
LADKI_BAHIN = {"Ladki Bahin Yojana (Maharashtra)", "Mukhyamantri Majhi Ladki Bahin Yojana (Maharashtra)"}

PHOTO_FIX = {"Passport size photograph": "Passport size photographs", "Photographs": "Passport size photographs",
             "2 passport size photographs": "Passport size photographs"}


def parse_helplines(text):
    out = []
    for part in re.split(r"\s*/\s*", (text or "").strip()):
        if not part or part.lower() in ("n/a", "na", "none"):
            continue
        m = re.match(r"^(.*?)\s*\((.*)\)\s*$", part)
        out.append(H(m.group(1).strip(), m.group(2).strip()) if m else H(part, ""))
    return out


def convert(legacy, ovr):
    url = legacy.get("application_url", "")
    url = url if url.startswith("http") else ""
    docs = []
    for d in legacy.get("documents", []):
        d = PHOTO_FIX.get(d, d)
        mand = False if re.search(r"\(if |\bif applicable\b|\(for ", d.lower()) else None
        docs.append(D(d, mand))
    is_central = legacy["scope"] == "central"
    return R(
        id=ovr["id"], name=legacy["name"], aliases=ovr.get("aliases", []),
        level=legacy["scope"], states=["ALL"] if is_central else list(legacy["states"]),
        domains=ovr.get("domains", []), summary=legacy["description"],
        beneficiary={"description": legacy["target_group"], "occupations": ovr.get("occupations", []),
                     "genders": ovr.get("genders", ["any"]), "social_categories": ovr.get("social_categories", [])},
        eligibility={"criteria": [legacy["eligibility"]], "exclusions": []},
        benefits={"summary": legacy["benefits"], "items": []},
        documents=docs,
        application={"modes": [], "steps": [], "portal_url": url},
        contacts={"helplines": parse_helplines(legacy.get("helpline"))},
        sources=[S("Portal URL carried over from the legacy dataset (not yet verified)", url, "unverified")] if url else [],
        notes=(ovr["notes"] + " " if ovr.get("notes") else "") + MIGRATION_NOTE,
    )


def ladki_bahin():
    return R(
        id="mukhyamantri-majhi-ladki-bahin-yojana", name="Mukhyamantri Majhi Ladki Bahin Yojana (Maharashtra)",
        level="state", states=["Maharashtra"],
        aliases=["Ladki Bahin Yojana", "Ladki Bahin", "Majhi Ladki Bahin", "Ladki Bahin Yojana (Maharashtra)", "Mukhyamantri Majhi Ladki Bahin", "लाडकी बहीण योजना"],
        domains=["women_cash_transfer"],
        summary="Maharashtra government scheme giving monthly financial assistance to eligible women for economic empowerment and better household living standards.",
        beneficiary={"description": "Women of Maharashtra", "genders": ["women"], "age_min": 21, "age_max": 65},
        eligibility={"criteria": ["Women aged 21-65 years who are residents of Maharashtra, with annual family income below Rs. 2.5 lakh. Income-tax payers are not eligible."], "exclusions": []},
        benefits={"summary": "Monthly financial assistance of Rs. 1,500 (Rs. 18,000 per year) by direct benefit transfer to an Aadhaar-linked bank account. Covers married, widowed, divorced and abandoned women.",
                  "items": [{"type": "cash", "amount": 1500, "unit": "INR/month"}]},
        documents=[D("Aadhaar", None), D("Bank account details", None), D("Age proof", None), D("Income certificate", None), D("Domicile certificate", None)],
        application={"modes": [], "steps": [], "portal_url": "https://ladakibahin.maharashtra.gov.in/"},
        contacts={"helplines": [H("181"), H("1800-233-4475")]},
        sources=[S("Portal URL carried over from the legacy dataset (not yet verified)", "https://ladakibahin.maharashtra.gov.in/", "unverified")],
        notes="Merged from two duplicate legacy records (Ladki Bahin Yojana; Mukhyamantri Majhi Ladki Bahin Yojana). Only the eligibility rules common to both "
              "were kept; the extra '5 acres of land' rule (one record only) and the government-employee/pension rule (other record only) were dropped pending verification. "
              "Document list carried over from the legacy dataset's merged records; benefit amount and rules may have changed since - not re-verified. " + MIGRATION_NOTE,
    )


def main():
    legacy = json.load(open(LEGACY, encoding="utf-8"))["schemes"]
    out = []
    unmapped = []
    for row in legacy:
        n = row["name"]
        if n in HAND_AUTHORED_LEGACY or n in LADKI_BAHIN:
            continue
        if n not in NF:
            unmapped.append(n)
            continue
        out.append(convert(row, NF[n]))
    if unmapped:
        sys.exit("Unmapped legacy records: %s" % unmapped)
    out.append(ladki_bahin())
    out.extend(farmer_records())
    out.sort(key=lambda r: (0 if r["id"] in {x["id"] for x in farmer_records()} else 1, r["id"]))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 2, "schemes": out}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("wrote %d records to %s" % (len(out), OUT))


if __name__ == "__main__":
    main()
