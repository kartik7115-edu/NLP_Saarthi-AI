"""Stage 2, batch 1: central farm schemes (idempotent upsert into app/data/schemes.json).

Adds / replaces by id: agriculture-infrastructure-fund, e-nam, soil-health-card, pm-kusum,
pmksy-per-drop-more-crop, smam-farm-mechanisation, midh-horticulture, fpo-10000-scheme.

Verification policy used here
- "verified"     : every populated claim in the record was checked against an official government
                   page/document that was actually read (not a search snippet, not a secondary site).
- "needs_review" : anything else. Gaps are left empty and conflicts/uncertainties are written in `notes`.
Unknown fields (documents, application steps, URLs) are left empty rather than guessed.

Run from backend/:  python scripts/add_batch_stage2_central_farm.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from migrate_schemes_v1_to_v2 import D, H, R, S  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "app", "data", "schemes.json")
TODAY = "2026-10-06"


def records():
    recs = []

    # ------------------------------------------------------------------ AIF
    recs.append(R(
        id="agriculture-infrastructure-fund", name="Agriculture Infrastructure Fund (AIF)",
        aliases=["AIF", "Agri Infra Fund", "Agri Infrastructure Fund", "Agriculture Infrastructure Fund",
                 "National Agriculture Infra Financing Facility", "Krishi Infra Fund",
                 "कृषि अवसंरचना कोष", "एग्री इन्फ्रा फंड"],
        domains=["agri_infrastructure", "agri_credit"],
        summary="Central sector financing facility (Department of Agriculture and Farmers Welfare) that gives medium- and long-term "
                "loans through banks and other lenders for post-harvest management infrastructure and community farming assets, "
                "with a 3% per year interest subvention and credit guarantee support.",
        beneficiary={"description": "Farmers, FPOs, PACS and other cooperative societies, SHGs, JLGs, agri-entrepreneurs, start-ups and "
                                    "government-sponsored PPP projects (list taken from lender summaries of the official guidelines)",
                     "occupations": ["farmer"], "farmer_types": ["landowner", "tenant", "small", "marginal", "large"]},
        crops=["ALL"], activities=["storage", "marketing", "food_processing", "farmer_producer_organization"],
        eligibility={"criteria": [
            "The project must be a viable post-harvest management project (for example warehouses, cold storage, grading and sorting units, pack houses, ripening chambers) or a community farming asset, including infrastructure for smart and precision agriculture.",
            "The applicant must own the project land or hold a valid lease of it.",
            "The applicant must meet the lending institution's credit norms.",
            "Interest subvention and credit guarantee apply to loans up to Rs. 2 crore per project.",
            "Fresh loan disbursement under the scheme was scheduled to complete by the end of FY 2025-26, while the scheme itself runs to 2032-33 for subvention and guarantee support. Whether new loans are still being sanctioned in FY 2026-27 could not be confirmed - check with the lending bank."],
            "exclusions": []},
        benefits={"summary": "Loans from banks and financial institutions with 3% per year interest subvention on loans up to Rs. 2 crore for a maximum of 7 years "
                             "(including a moratorium of up to 2 years). Credit guarantee cover through CGTMSE for loans up to Rs. 2 crore, with the guarantee fee "
                             "borne by the Government; for FPOs the guarantee can come from the facility under the FPO promotion scheme. PIB states the interest rate "
                             "on these loans is capped at 9%. A collateral-free term loan of up to Rs. 2 crore is available.",
                  "items": [
                      {"type": "interest_subvention", "amount": 3, "unit": "% per year", "note": "On loans up to Rs. 2 crore; maximum 7 years including a moratorium of up to 2 years."},
                      {"type": "credit_guarantee", "amount": 20000000, "unit": "INR", "note": "CGTMSE cover for loans up to Rs. 2 crore; fee borne by the Government. FPOs use the FPO-scheme facility."},
                      {"type": "collateral_free_limit", "amount": 20000000, "unit": "INR", "note": "Collateral-free term loan up to Rs. 2 crore (Lok Sabha reply, 6 Aug 2024)."},
                      {"type": "interest_rate_cap", "amount": 9, "unit": "% per year", "note": "Capped interest rate on AIF loans (PIB)."},
                      {"type": "loan_provision", "amount": 1000000000000, "unit": "INR", "note": "Rs. 1 lakh crore total financing provision."}]},
        documents=[D("KYC documents of the applicant", None, "Listed on lender pages; the official checklist on the AIF portal could not be read."),
                   D("Detailed Project Report (DPR)", None, "Uploaded on the AIF portal after registration (lender page)."),
                   D("Land ownership or lease documents", None, "Inferred from the land condition in the official eligibility; confirm the exact list with the lender.")],
        application={"modes": ["online", "bank"],
                     "steps": ["Register on the AIF portal (agriinfra.dac.gov.in) and upload the Detailed Project Report and related documents (lender summaries).",
                               "A loan can also be applied for offline at the lending institution, but the lender must enter it on the online platform to obtain the AIF benefits (myScheme FAQ).",
                               "The decision on a loan application is to be conveyed within a maximum of 60 days from the date of application (myScheme FAQ)."],
                     "portal_url": "https://agriinfra.dac.gov.in/"},
        sources=[S("PIB, 3 Feb 2026: AIF strengthens post-harvest infrastructure through interest-subvented loans", "https://www.pib.gov.in/PressReleseDetailm.aspx?PRID=2222807&reg=3&lang=2", "press_release"),
                 S("PIB: Agriculture Infrastructure Fund (2025 note)", "https://www.pib.gov.in/PressNoteDetails.aspx?ModuleId=3&NoteId=154999&reg=3&lang=2", "press_release"),
                 S("Lok Sabha Unstarred Question 2398, 6 Aug 2024 (collateral-free loan up to Rs. 2 crore, 3% subvention)", "https://eparlib.nic.in/bitstream/123456789/2979616/1/AU2398_Tm04WT.pdf", "official"),
                 S("Operational guidelines copy hosted by Maharashtra Agriculture Department", "https://krishi.maharashtra.gov.in/Site/Upload/Pdf/AIF.pdf", "official"),
                 S("Government of West Bengal: scheme page", "https://wb.gov.in/government-schemes-details-agriculture-infrastructure-fund.aspx", "official"),
                 S("myScheme (Government of India): AIF", "https://www.myscheme.gov.in/schemes/aif", "official"),
                 S("Federal Bank: AIF summary (applicant list, DPR upload)", "https://federal.bank.in/agriculture-infrastructure-fund", "secondary")],
        notes="Needs review: (1) the AIF portal agriinfra.dac.gov.in blocks automated access, so the official document checklist and application screens were not read; "
              "(2) the fresh-disbursement window officially ended with FY 2025-26 and no official extension was found; (3) the applicant list comes from lender summaries and "
              "the 'maximum 25 projects per entity' limit appears only in lender/news sources, so neither is recorded as a rule; (4) one source gives the scheme period as FY2020-FY2032 "
              "and PIB as 2020-21 to 2032-33 - PIB figure used.",
    ))

    # ----------------------------------------------------------------- e-NAM
    recs.append(R(
        id="e-nam", name="e-NAM (National Agriculture Market)",
        aliases=["eNAM", "e-NAM", "e NAM", "National Agriculture Market", "Electronic National Agriculture Market",
                 "ई-नाम", "राष्ट्रीय कृषि बाजार"],
        domains=["agricultural_marketing"],
        summary="Online trading platform of the Department of Agriculture and Farmers Welfare that links e-NAM-enabled mandis (APMCs) so that "
                "farmers, traders and FPOs can register and trade through transparent bidding with payments directly into bank accounts.",
        beneficiary={"description": "Farmers (sellers), traders and FPOs/FPCs trading at e-NAM-enabled mandis",
                     "occupations": ["farmer"], "farmer_types": ["small", "marginal", "large"]},
        crops=["NOTIFIED_CROPS"], activities=["marketing", "farmer_producer_organization"],
        eligibility={"criteria": [
            "Farmers can register on e-NAM as sellers and choose the APMC/mandi where they want to trade; the selected APMC approves the registration after KYC.",
            "Trading takes place at mandis that are integrated with e-NAM. A state integrates its mandis by adopting a single unified trading licence, a single-point market fee and e-auction/e-trading as a mode of price discovery.",
            "Only commodities notified for e-NAM trading at the mandi can be traded there, so crop coverage depends on the mandi.",
            "FPOs/FPCs can register on the e-NAM portal or mobile app, or at the nearest e-NAM mandi, by giving the FPO name, the name, address, email and contact number of the authorised person (MD/CEO/Manager) and bank details."],
            "exclusions": []},
        benefits={"summary": "Registration is free. Farmers get better price discovery through transparent bidding, access to more markets and buyers, real-time "
                             "information on prices and arrivals in nearby mandis, and quick payments by RTGS, NEFT or BHIM UPI directly into their bank accounts. "
                             "Assaying lets the price reflect the produce quality, and each arrival is digitally recorded with a unique lot ID that can be tracked from a mobile phone.",
                  "items": [{"type": "fee", "amount": 0, "unit": "INR", "note": "No fee for registration on e-NAM."},
                            {"type": "payment_modes", "note": "Instant payment into the seller's bank account via RTGS, NEFT or BHIM UPI."}]},
        documents=[D("Mandatory details: name, sex, address, date of birth, mobile number and bank details", True, "Details asked at registration."),
                   D("Bank passbook (or cancelled cheque leaf)", True, "Official page: 'Passbook (Cheque leaf)'."),
                   D("Any government identity proof", True, "Official page does not specify Aadhaar; some secondary sites say Aadhaar - the official wording is used.")],
        application={"modes": ["online", "offline"],
                     "steps": ["Register on the e-NAM portal (enam.gov.in), through the e-NAM mobile app, or at the gate of an e-NAM mandi; there is no fee.",
                               "On the portal, choose Registration Type 'Farmer', select the desired APMC and give a correct email ID, where the temporary login ID and password are sent.",
                               "Log in to the dashboard, open 'Click here to register with APMC', and fill in or update your details; the application goes to the selected APMC for approval after KYC.",
                               "Track the status (Submitted / In progress / Approved / Rejected) by email or on the dashboard.",
                               "After the APMC approves, you receive a permanent Farmer Login ID and password by email; you can also contact the mandi/APMC directly."],
                     "portal_url": "https://enam.gov.in/"},
        contacts={"helplines": [H("1800 270 0224", "e-NAM helpline shown on the portal")]},
        sources=[S("e-NAM: Registration Guidelines (official portal)", "https://enam.gov.in/resources/registration-guideline", "official"),
                 S("e-NAM: Farmers page - registration, fees, documents, benefits (official portal)", "https://enam.gov.in/stakeholders-Involved/farmers", "official")],
        last_verified=TODAY, confidence="verified",
        notes="Verified on 2026-10-06 against the official e-NAM pages listed in sources (both pages were read in full). "
              "Conflict resolved in favour of the official page: secondary sites list Aadhaar and land documents, while the official page asks for a bank passbook/cheque leaf and 'any government identity proof'. "
              "Not covered by the official pages read: launch date, the list of commodities per mandi, and which states have integrated mandis.",
    ))

    # ------------------------------------------------------------ Soil Health Card
    recs.append(R(
        id="soil-health-card", name="Soil Health Card Scheme (Soil Health & Fertility component of RKVY)",
        aliases=["Soil Health Card", "Soil Health Card Scheme", "SHC", "SHC Scheme", "Soil Health & Fertility",
                 "Soil Health and Fertility", "मृदा स्वास्थ्य कार्ड", "मिट्टी जांच कार्ड"],
        domains=["soil_health"],
        summary="Scheme that helps state governments issue Soil Health Cards to farmers: a report on the nutrient status of each land holding with "
                "recommendations on fertilisers, bio-fertilisers and soil treatments. Launched on 19 February 2015; since 2022-23 it is a component "
                "of the Rashtriya Krishi Vikas Yojana (RKVY) called 'Soil Health & Fertility'.",
        beneficiary={"description": "All farmers (a card is issued for each land holding)", "occupations": ["farmer"],
                     "farmer_types": ["small", "marginal", "large"]},
        crops=["ALL"], activities=["crop_cultivation"],
        eligibility={"criteria": [
            "The scheme is applicable to all farmers in India (cited by PIB from NITI Aayog).",
            "A Soil Health Card is issued for each land holding."], "exclusions": []},
        benefits={"summary": "Soil testing of the holding on 12 parameters (N, P, K, S; Zn, Fe, Cu, Mn, B; pH, EC, organic carbon) and a printed/digital Soil Health Card with "
                             "recommendations on the right amount of fertilisers, bio-fertilisers, organic inputs and soil treatments. The Central Government provides Rs. 190 per sample to the "
                             "states to cover collection, testing, card generation and distribution. Cards are available in 22 languages.",
                  "items": [{"type": "central_support_per_sample", "amount": 190, "unit": "INR/sample", "note": "Paid to states for collection, testing, card generation and distribution."},
                            {"type": "soil_parameters_tested", "amount": 12, "unit": "parameters"}]},
        documents=[D("Aadhaar", None, "A secondary source says the card can be looked up on the portal with an Aadhaar number; not stated in the PIB document.")],
        application={"modes": ["agriculture_department"],
                     "steps": ["Soil samples are collected from the field by trained personnel, agriculture department staff or agriculture-college students, using GPS-tagged grid sampling (2.5 hectares in irrigated areas, 10 hectares in rain-fed areas), after harvest or when no crop is standing (PIB).",
                               "Samples are tested in approved soil testing laboratories; results go to the national Soil Health Card portal, which generates the card with fertiliser recommendations (PIB).",
                               "To ask for sampling or to get your card, contact your local agriculture office, KVK or soil testing lab (secondary sources; not stated in the PIB document)."],
                     "portal_url": "https://soilhealth.dac.gov.in/"},
        sources=[S("PIB backgrounder, 16 Aug 2025: Soil Health Card", "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/aug/doc2025816613501.pdf", "press_release"),
                 S("PIB factsheet: Soil Health Card", "https://www.pib.gov.in/FactsheetDetails.aspx?Id=148602", "press_release"),
                 S("Delhi District Magistrate page: Soil Health Card", "https://dmcentralnorth.delhi.gov.in/?p=27635", "official"),
                 S("Organic Mandya: how to get a Soil Health Card", "https://farming.organicmandya.com/india/soil-health-card-scheme/", "secondary")],
        notes="Needs review. Conflict inside the official PIB document (16 Aug 2025): its objectives say a card is provided once every two years and the testing guidance is 'every 2 years', "
              "but its 'Card validity' section says a card is issued once every 3 years - not resolved, so no card frequency is recorded as a rule. "
              "The PIB document does not say explicitly that testing is free for the farmer (secondary sources do). "
              "The scheme was merged into RKVY as 'Soil Health & Fertility' from 2022-23 (PIB), so it is not a standalone scheme any more. "
              "Village-level soil testing labs (separate entrepreneur opportunity): the PIB 2025 document gives applicant age 18-27 under the 22 Jun 2023 guidelines, "
              "an older PIB factsheet says up to 40 - the later source is preferred but not recorded in eligibility. Official portal soilhealth.dac.gov.in could not be read directly.",
    ))

    # ------------------------------------------------------------------- PM-KUSUM
    recs.append(R(
        id="pm-kusum", name="PM-KUSUM (Pradhan Mantri Kisan Urja Suraksha evam Utthaan Mahabhiyaan)",
        aliases=["PM-KUSUM", "PM KUSUM", "PMKUSUM", "KUSUM", "KUSUM Yojana", "PM Kusum Yojana", "PM-KUSUM Scheme",
                 "पीएम कुसुम", "प्रधानमंत्री कुसुम योजना"],
        domains=["solar_energy", "irrigation"],
        summary="MNRE scheme to de-dieselise and solarise agriculture: Component A (small grid-connected solar plants of 500 kW-2 MW), Component B "
                "(standalone solar agriculture pumps up to 7.5 HP) and Component C (solarisation of grid-connected pumps, individually or by feeder).",
        beneficiary={"description": "Individual farmers (Components B and C-IPS); farmers, groups of farmers, cooperatives, panchayats, FPOs and Water User Associations (Component A)",
                     "occupations": ["farmer"], "farmer_types": ["small", "marginal", "large"]},
        crops=["ALL"], activities=["solar_agriculture", "irrigation"],
        eligibility={"criteria": [
            "Component B: individual farmers can install standalone solar agriculture pumps of up to 7.5 HP in off-grid areas where grid supply is not available (replacing diesel pumps). Pumps above 7.5 HP are allowed but central support is limited to the 7.5 HP level.",
            "Component C (Individual Pump Solarisation): farmers with an existing grid-connected agriculture pump of up to 7.5 HP; solar PV capacity up to two times the pump capacity in kW is allowed; surplus power can be sold to the DISCOM.",
            "Component A: renewable power plants of 500 kW-2 MW can be set up by individual farmers, groups of farmers, cooperatives, panchayats, FPOs or Water User Associations (or through a developer or the local DISCOM if they cannot arrange the equity).",
            "Component C (Feeder Level Solarisation) is carried out by states/DISCOMs and is not an individual-farmer application.",
            "Applicability in a given year depends on the state: MNRE gives each state an allocation and the state implementing agency invites and processes applications.",
            "The MNRE page lists the scheme period as 'Till 31.03.2026'. Later MNRE memoranda reported in the press extended project timelines for already-sanctioned projects and PM-KUSUM 2.0 is still being finalised - confirm with your state implementing agency whether applications are open."],
            "exclusions": []},
        benefits={"summary": "Components B and C (IPS), all states except those listed next: Central Financial Assistance of 30% of the benchmark or tender cost (whichever is lower), "
                             "state subsidy of at least 30%, and at most 40% paid by the farmer (bank finance can be used, so the farmer initially pays about 10%). "
                             "In the North Eastern states, Sikkim, Jammu & Kashmir, Himachal Pradesh, Uttarakhand, Lakshadweep and Andaman & Nicobar Islands: central assistance of 50%, state subsidy of at least 30%, and at most 20% paid by the farmer. "
                             "Farmers may install pumps with central assistance even where a state does not provide its share.",
                  "items": [
                      {"type": "central_subsidy", "amount": 30, "unit": "% of benchmark/tender cost", "note": "Components B and C (IPS), general states."},
                      {"type": "state_subsidy_min", "amount": 30, "unit": "% of cost", "note": "Components B and C (IPS)."},
                      {"type": "farmer_share_max", "amount": 40, "unit": "% of cost", "note": "Components B and C (IPS), general states; bank finance allowed."},
                      {"type": "central_subsidy", "amount": 50, "unit": "% of benchmark/tender cost", "note": "North Eastern states, Sikkim, J&K, Himachal Pradesh, Uttarakhand, Lakshadweep, A&N Islands."},
                      {"type": "farmer_share_max", "amount": 20, "unit": "% of cost", "note": "Same special-category states and UTs."}]},
        documents=[],
        application={"modes": [],
                     "steps": ["MNRE issues state-wise allocations after approval by a Screening Committee; individual farmers apply through their state implementing agency (see 'Whom to contact' and the state implementing agencies list on the national PM-KUSUM portal).",
                               "MNRE central assistance and the state subsidy are adjusted in the system cost, so the farmer pays only the remaining balance (MNRE page)."],
                     "portal_url": "https://pmkusum.mnre.gov.in/"},
        sources=[S("MNRE: PM-KUSUM scheme page (last updated 7 Nov 2025)", "https://mnre.gov.in/en/pradhan-mantri-kisan-urja-suraksha-evam-utthaan-mahabhiyaan-pm-kusum/", "official"),
                 S("National PM-KUSUM portal: FAQ (content seen only in a search result; the page did not render)", "https://pmkusum.mnre.gov.in/landing-faq.html", "official"),
                 S("Renewable Watch, 31 Mar 2026: MNRE extends timelines for projects under PM KUSUM", "https://renewablewatch.in/2026/03/31/mnre-extends-timelines-for-projects-under-pm-kusum-scheme/", "secondary"),
                 S("Energetica India: MNRE extends PM-KUSUM financial closure deadline to Nov 30", "https://energetica-india.net/news/mnre-extends-pm-kusum-financial-closure-deadline-to-nov-30", "secondary")],
        notes="Needs review. The subsidy structure, eligibility by component and the application route were read on the official MNRE page, but (1) that page was last updated on 7 Nov 2025 and still shows the period "
              "as 'Till 31.03.2026'; (2) press reports of MNRE memoranda (March 2026) extend only the timelines of already-sanctioned projects - and give conflicting dates (Component B/C-IPS commissioning by 30 Sep 2026; "
              "financial closure for Components A and C-FLS by 30 Sep 2026 in one report and 30 Nov 2026 in a later one; completion by 31 Mar 2027) - so whether new farmer applications are being accepted is unconfirmed; "
              "(3) PM-KUSUM 2.0 (including a proposed Agri-PV component) has been announced as under consideration but its terms are not available; "
              "(4) documents and application steps are state-specific and are not on the central page. One page (a secondary summary) puts capacity support up to 15 HP in some regions; "
              "an MNRE amendment reported in 2022 allows support for pumps up to 15 HP for individual farmers in the North East, hill states and island UTs, limited to 10% of installations - not recorded because the current MNRE page only states 7.5 HP.",
    ))

    # --------------------------------------------------------------- PMKSY / PDMC
    recs.append(R(
        id="pmksy-per-drop-more-crop", name="Pradhan Mantri Krishi Sinchayee Yojana (PMKSY) - Per Drop More Crop (micro-irrigation)",
        aliases=["PMKSY", "Pradhan Mantri Krishi Sinchayee Yojana", "PM Krishi Sinchayee Yojana", "PM Krishi Sinchai Yojana",
                 "Per Drop More Crop", "PDMC", "PMKSY-PDMC", "प्रधानमंत्री कृषि सिंचाई योजना", "पर ड्रॉप मोर क्रॉप"],
        domains=["irrigation", "water_management"],
        summary="Farm-level micro-irrigation (drip and sprinkler) support under the Per Drop More Crop component of PMKSY, which has been part of the "
                "Pradhan Mantri Rashtriya Krishi Vikas Yojana (PM-RKVY) since 2022-23. Small and marginal farmers get 55% assistance and other farmers 45%.",
        beneficiary={"description": "Farmers installing micro-irrigation systems (small and marginal farmers get a higher rate)",
                     "occupations": ["farmer"], "farmer_types": ["small", "marginal", "large"]},
        crops=["ALL"], activities=["irrigation"],
        eligibility={"criteria": [
            "Farmers who install micro-irrigation systems (drip, sprinkler, rain-gun) on their land; assistance is limited to an overall ceiling of 5 hectares per beneficiary and to the unit costs specified in the guidelines.",
            "Per Drop More Crop is implemented through states/UTs; beneficiary selection, land and registration requirements are set by each state and are not stated in the central sources read.",
            "PMKSY's other components (Accelerated Irrigation Benefit Programme, Har Khet Ko Pani and Watershed Development) fund irrigation projects and are not applied for by individual farmers."],
            "exclusions": []},
        benefits={"summary": "Financial assistance of 55% of the cost of a micro-irrigation system for small and marginal farmers and 45% for other farmers, limited to 5 hectares per beneficiary "
                             "and to the unit costs in the guidelines. Subsidy is released to the beneficiary electronically. Some states add their own top-ups (secondary source).",
                  "items": [{"type": "subsidy", "amount": 55, "unit": "% of system cost", "note": "Small and marginal farmers; ceiling 5 hectares per beneficiary."},
                            {"type": "subsidy", "amount": 45, "unit": "% of system cost", "note": "Other farmers; ceiling 5 hectares per beneficiary."},
                            {"type": "area_ceiling", "amount": 5, "unit": "hectares per beneficiary"}]},
        documents=[],
        application={"modes": [],
                     "steps": ["Per Drop More Crop is implemented through states/UTs: ask your state agriculture or horticulture department how to apply and which portal to use. The central sources read do not describe the application steps."],
                     "portal_url": "https://pmksy.gov.in/"},
        sources=[S("PIB backgrounder, 30 Jun 2026: PMKSY - a decade of irrigation-led transformation", "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jun/doc2026630906701.pdf", "press_release"),
                 S("PIB, 29 Aug 2026: Per Drop More Crop", "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/aug/doc_9732_20260829_12330401.pdf", "press_release"),
                 S("Parliament reply on PMKSY (55%/45%, 5 hectare ceiling)", "https://eparlib.nic.in/bitstream/123456789/802386/1/AS228.pdf", "official"),
                 S("PMKSY portal: FAQ (content seen in a search result)", "https://Pmksy.gov.in/pdfLinks/FAQ.pdf", "official")],
        notes="Needs review. The 55%/45% rates, the 5-hectare ceiling and the PM-RKVY placement are confirmed by PIB (30 Jun 2026) and a Parliament reply. Not confirmed: "
              "(1) PMKSY's continuation was approved for 2021-26 and Union Budget 2026-27 allocated Rs. 6,587 crore, but no approval for the period after March 2026 was found; "
              "(2) required documents and application steps (state-specific); (3) the PMKSY portal and the PDMC portal could not be read. "
              "This record covers only the farmer-facing micro-irrigation component; the alias 'PMKSY' resolves here because it is the only part individual farmers apply for.",
    ))

    # ----------------------------------------------------------------------- SMAM
    recs.append(R(
        id="smam-farm-mechanisation", name="Sub-Mission on Agricultural Mechanization (SMAM)",
        aliases=["SMAM", "Sub-Mission on Agricultural Mechanization", "Sub Mission on Agricultural Mechanization",
                 "Sub-Mission on Agricultural Mechanisation", "कृषि यंत्रीकरण उप-मिशन"],
        domains=["farm_mechanisation"],
        summary="Centrally sponsored scheme under the RKVY umbrella (launched 2014-15) that gives subsidy for buying farm machinery and supports Custom Hiring Centres, "
                "Farm Machinery Banks and Hi-Tech Hubs, with a focus on small and marginal farmers, women, SC/ST farmers and the North East.",
        beneficiary={"description": "Farmers (higher rate for SC/ST, small and marginal farmers and North Eastern states), women farmers (30% of funds earmarked), FPOs, SHGs, cooperatives and rural entrepreneurs",
                     "occupations": ["farmer"], "farmer_types": ["small", "marginal", "large", "women"]},
        crops=["ALL"], activities=["farm_mechanisation", "farmer_producer_organization"],
        eligibility={"criteria": [
            "Individual farmers can receive a subsidy on eligible farm machinery through Direct Benefit Transfer; the rate is higher for SC/ST farmers, small and marginal farmers and beneficiaries in North Eastern states.",
            "Women farmers are part of the target group and 30% of total funds are earmarked for them.",
            "FPOs, SHGs, cooperatives and rural entrepreneurs can set up Custom Hiring Centres and Farm Machinery Banks.",
            "The scheme runs through state Annual Action Plans: the Centre-State funding pattern is 60:40 for most states, 90:10 for North Eastern and Himalayan states and 100% Central for Union Territories."],
            "exclusions": ["Revised Operational Guidelines (18 Dec 2025), excerpt only partly readable: machines such as tractors above 40 HP and combine harvesters appear to be promoted only through Custom Hiring Centres / Farm Machinery Banks rather than individual subsidy - confirm the exact wording and machine list."]},
        benefits={"summary": "Subsidy of 40% of the machine cost for general beneficiaries and 50% for SC/ST farmers, small and marginal farmers and beneficiaries in North Eastern states. "
                             "Small and marginal farmers receive Rs. 2,000 per hectare for mechanised services (including drones) through Custom Hiring Centres, SHGs and FPOs. "
                             "Custom Hiring Centres get 40% of project cost for projects up to Rs. 250 lakh; Farm Machinery Banks get 80% for projects up to Rs. 30 lakh (up to 95% in the North East); "
                             "the North East gets up to 100% subsidy on small machinery.",
                  "items": [{"type": "subsidy", "amount": 40, "unit": "% of machine cost", "note": "General beneficiaries."},
                            {"type": "subsidy", "amount": 50, "unit": "% of machine cost", "note": "SC/ST farmers, small and marginal farmers and North Eastern beneficiaries (PIB, 9 Jul 2026)."},
                            {"type": "service_support", "amount": 2000, "unit": "INR/hectare", "note": "For mechanised services including drones, small and marginal farmers, via CHCs, SHGs and FPOs."},
                            {"type": "chc_subsidy", "amount": 40, "unit": "% of project cost", "note": "Custom Hiring Centres, projects up to Rs. 250 lakh."},
                            {"type": "fmb_subsidy", "amount": 80, "unit": "% of project cost", "note": "Farm Machinery Banks, projects up to Rs. 30 lakh."}]},
        documents=[],
        application={"modes": ["online", "agriculture_department"],
                     "steps": ["Application, sanction and release of subsidy for individual machinery are done only through the online DBT portal of the Government of India (state agriculture department page).",
                               "Check with your state agriculture or agricultural-engineering department for the Annual Action Plan, the eligible machine list and local rules."],
                     "portal_url": ""},
        sources=[S("PIB, 9 Jul 2026: Sub-Mission on Agricultural Mechanization", "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202679916301.pdf", "press_release"),
                 S("Himachal Pradesh Agriculture Department: SMAM", "https://agriculture.hp.gov.in/?p=6805", "official"),
                 S("SMAM Revised Operational Guidelines dated 18.12.2025 (copy hosted by HP Agriculture Department; excerpt only)", "https://agriculture.hp.gov.in/wp-content/uploads/2026/02/SMAM-Revised-Operational-Guidelines-dated-18-12-2025.pdf", "official"),
                 S("Lok Sabha reply on SMAM and Custom Hiring Centres", "https://eparlib.sansad.in/bitstream/123456789/959600/1/AU3473.pdf", "official")],
        notes="Needs review. Conflict not resolved: PIB (9 Jul 2026) lists the 50% rate for SC/ST farmers, small and marginal farmers and North Eastern beneficiaries and earmarks 30% of funds for women, "
              "whereas a Lok Sabha reply, the Himachal Pradesh Agriculture Department page and an excerpt of the 2025 guidelines list women farmers among those getting 50% - so the women rate is not recorded as a rule. "
              "PIB gives Farm Machinery Bank support as '80-90%' in one place and '80% up to Rs. 30 lakh' in another. "
              "State schemes may add their own rates (a Tamil Nadu page adds 20 percentage points for SC/ST small and marginal farmers). "
              "The required documents and the DBT portal URL could not be confirmed from sources that could be read.",
    ))

    # ----------------------------------------------------------------------- MIDH
    recs.append(R(
        id="midh-horticulture", name="Mission for Integrated Development of Horticulture (MIDH)",
        aliases=["MIDH", "Mission for Integrated Development of Horticulture", "Horticulture Mission",
                 "बागवानी एकीकृत विकास मिशन"],
        domains=["horticulture", "post_harvest_infrastructure"],
        summary="Centrally sponsored mission of the Department of Agriculture and Farmers Welfare for the development of horticulture, including "
                "post-harvest infrastructure such as cold storages, delivered through State Horticulture Missions under their Annual Action Plans.",
        beneficiary={"description": "Individuals, groups of farmers/growers, firms, SHGs, FPOs, companies, cooperatives, local bodies, APMCs and State Governments (as listed for the cold-storage component)",
                     "occupations": ["farmer"], "farmer_types": ["small", "marginal", "large"]},
        crops=["horticultural_crops"], activities=["horticulture", "storage"],
        eligibility={"criteria": [
            "Assistance for cold storage (construction, expansion, modernisation of capacity up to 5,000 MT) is available to individuals, groups of farmers/growers/consumers, partnership and proprietary firms, SHGs, FPOs, companies, corporations, cooperatives, cooperative marketing federations, local bodies, APMCs and marketing boards, and State Governments.",
            "The cold-storage component is demand/entrepreneur driven and the subsidy is credit-linked: it works with a bank loan and is routed through the State Horticulture Mission on the basis of the state's Annual Action Plan.",
            "Cold storages above 5,000 MT and up to 10,000 MT are covered by a separate National Horticulture Board scheme, not by MIDH.",
            "MIDH covers many other horticulture activities; only the cold-storage component could be confirmed from the official sources read."],
            "exclusions": []},
        benefits={"summary": "Cold storage (up to 5,000 MT): credit-linked back-ended subsidy of 35% of the project cost in general areas and 50% in hilly and scheduled areas (Lok Sabha reply, 6 Aug 2024). "
                             "Subsidy rates for other MIDH components (planting material, area expansion, protected cultivation and so on) are not recorded.",
                  "items": [{"type": "credit_linked_subsidy", "amount": 35, "unit": "% of project cost", "note": "Cold storage up to 5,000 MT, general areas."},
                            {"type": "credit_linked_subsidy", "amount": 50, "unit": "% of project cost", "note": "Cold storage up to 5,000 MT, hilly and scheduled areas."}]},
        documents=[],
        application={"modes": ["agriculture_department"],
                     "steps": ["Apply through your State Horticulture Mission/Department; the subsidy is credit-linked, so a bank loan for the project is part of the process. Application steps and forms differ by state and are not described in the sources read."],
                     "portal_url": ""},
        sources=[S("Lok Sabha Unstarred Question 2398, 6 Aug 2024 (MIDH cold storage subsidy and beneficiaries)", "https://eparlib.nic.in/bitstream/123456789/2979616/1/AU2398_Tm04WT.pdf", "official"),
                 S("Rajya Sabha reply, 23 Sep 2020 (MIDH 35% / 50%)", "https://rsdebate.nic.in/bitstream/123456789/713342/2/PQ_252_23092020_U1454_p100_p101.pdf", "official"),
                 S("Assam Directorate of Horticulture: MIDH post-harvest management (guideline excerpt)", "https://dirhorti.assam.gov.in/sites/default/files/swf_utility_folder/departments/horticulture_medhassu_in_oid_5/menu/information_and_services/Post%20Harvest%20Management_0.pdf", "official")],
        notes="Needs review. Conflict: the Parliament replies (2020 and 2024) give 35% (general) / 50% (hilly and scheduled) for cold storage, while an excerpt of MIDH guidelines on an Assam government page appears to show 40% for some post-harvest components - "
              "the excerpt was too garbled to resolve, so the 35%/50% figure from the 2024 Parliament reply is recorded. "
              "Scope is deliberately limited to the cold-storage component; the many other MIDH components are not covered. "
              "Where MIDH sits after the 2024-25 restructuring of agriculture schemes was not verified. "
              "Crop applicability is recorded as the category 'horticultural_crops' because the sources read do not list individual crops; crop-to-category matching is needed in retrieval. "
              "No portal URL, documents list or state-by-state application procedure could be confirmed.",
    ))

    # ------------------------------------------------------------------ FPO scheme
    recs.append(R(
        id="fpo-10000-scheme", name="Formation and Promotion of 10,000 Farmer Producer Organizations (FPO Scheme)",
        aliases=["10000 FPO scheme", "10,000 FPO scheme", "FPO scheme", "FPO Yojana", "Formation and Promotion of FPOs",
                 "Farmer Producer Organisation scheme", "Formation and Promotion of 10,000 FPOs", "एफपीओ योजना"],
        domains=["farmer_collectives"],
        summary="Central Sector Scheme launched on 29 February 2020 (budget Rs. 6,865 crore) to form and promote 10,000 Farmer Producer Organizations in produce clusters, "
                "through implementing agencies and Cluster-Based Business Organizations.",
        beneficiary={"description": "Farmers who become members of an FPO formed in a produce cluster; the support goes to the FPO",
                     "occupations": ["farmer"], "farmer_types": ["small", "marginal", "large"]},
        crops=["ALL"], activities=["farmer_producer_organization", "marketing"],
        eligibility={"criteria": [
            "Farmers do not apply individually: FPOs are formed in produce clusters by implementing agencies (SFAC, NABARD and NCDC, plus six more agencies approved later) through Cluster-Based Business Organizations (CBBOs) that aggregate farmers, register the FPO and provide handholding for 5 years (PIB, March 2021).",
            "Minimum membership at the time of the Cabinet approval was 300 farmers in plain areas and 100 in North Eastern and hilly areas (secondary reports of the Cabinet decision).",
            "Formation of the 10,000 FPOs has been reported as completed (secondary source); whether new FPOs can still be formed under this scheme could not be confirmed - ask SFAC, NABARD or NCDC."],
            "exclusions": []},
        benefits={"summary": "Per FPO: financial assistance of up to Rs. 18 lakh for 3 years; matching equity grant of up to Rs. 2,000 per farmer member with a limit of Rs. 15 lakh per FPO; "
                             "and a credit guarantee facility up to Rs. 2 crore of project loan from an eligible lending institution. Professional handholding by a CBBO for 5 years.",
                  "items": [{"type": "financial_assistance", "amount": 1800000, "unit": "INR per FPO", "note": "For 3 years."},
                            {"type": "equity_grant", "amount": 2000, "unit": "INR per farmer member", "note": "Matching grant; limit Rs. 15 lakh per FPO."},
                            {"type": "equity_grant_limit", "amount": 1500000, "unit": "INR per FPO"},
                            {"type": "credit_guarantee", "amount": 20000000, "unit": "INR project loan per FPO"}]},
        documents=[],
        application={"modes": [],
                     "steps": ["Contact an implementing agency (SFAC, NABARD, NCDC or another approved agency) or the CBBO engaged for your cluster; the sources read do not describe a direct application procedure."],
                     "portal_url": ""},
        sources=[S("PIB, 1 Mar 2021: first anniversary of the Central Sector Scheme on Formation and Promotion of 10,000 FPOs", "https://pib.gov.in/PressReleseDetailm.aspx?PRID=1701767", "press_release"),
                 S("Biovoice: Cabinet brief of the scheme (membership minimums, credit guarantee funds)", "https://biovoicenews.com/govt-okays-scheme-to-form-10000-new-farmer-producer-organizations/", "secondary"),
                 S("C4S Courses: scheme summary (reports completion of 10,000 FPOs)", "https://c4scourses.in/agriculture/central-sector-scheme-of-formation-and-promotion-of-10000-farmer-producer-organizations-fpos/", "secondary")],
        notes="Needs review. The benefit figures come from a PIB release dated March 2021; no more recent official source was read, so the current status of the scheme (formation target reportedly completed; "
              "scheme period reported as 2019-20 to 2023-24 with handholding continuing) is unconfirmed. Minimum membership comes from secondary reports of the Cabinet decision. "
              "No official application procedure, documents list or portal was found for individual farmers.",
    ))
    return recs


def main():
    data = json.load(open(OUT, encoding="utf-8"))
    schemes = data["schemes"]
    index = {s["id"]: i for i, s in enumerate(schemes)}
    added = replaced = 0
    for r in records():
        if r["id"] in index:
            schemes[index[r["id"]]] = r
            replaced += 1
        else:
            schemes.append(r)
            index[r["id"]] = len(schemes) - 1
            added += 1
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("added %d, replaced %d; total %d records" % (added, replaced, len(schemes)))


if __name__ == "__main__":
    main()
