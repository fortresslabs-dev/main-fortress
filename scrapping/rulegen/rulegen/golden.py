"""What I produced by hand on five sections, as the measuring stick.

These are the rules I wrote by reading the CFR text. The program's job
is to reproduce them. If it finds fewer duties, the prompt is weak; if
it invents duties I did not find, the prompt is loose.

Use evaluate.py to score a run against this.
"""

SECTIONS = {
"31 CFR 1020.210": {
 "url": "https://www.ecfr.gov/current/title-31/subtitle-B/chapter-X/part-1020/subpart-B/section-1020.210",
 "text": """(a) Anti-money laundering program requirements for banks regulated by a Federal functional regulator, including banks, savings associations, and credit unions. A bank regulated by a Federal functional regulator shall be deemed to satisfy the requirements of 31 U.S.C. 5318(h)(1) if it implements and maintains an anti-money laundering program that: (1) Complies with the requirements of 1010.610 and 1010.620 of this chapter; and (2) Includes, at a minimum: (i) A system of internal controls to assure ongoing compliance; (ii) Independent testing for compliance to be conducted by bank personnel or by an outside party; (iii) Designation of an individual or individuals responsible for coordinating and monitoring day-to-day compliance; (iv) Training for appropriate personnel; and (v) Appropriate risk-based procedures for conducting ongoing customer due diligence, to include, but not be limited to: (A) Understanding the nature and purpose of customer relationships for the purpose of developing a customer risk profile; and (B) Conducting ongoing monitoring to identify and report suspicious transactions and, on a risk basis, to maintain and update customer information.""",
 "expected_duties": 7,
 "expected_paths": ["(a) (1)", "(a) (2) (i)", "(a) (2) (ii)", "(a) (2) (iii)",
                    "(a) (2) (iv)", "(a) (2) (v)", "(a) (2) (v) (A)", "(a) (2) (v) (B)"],
 "expected_evidence": {"policy document", "audit report", "training record", "cdd system"},
 "expected_numbers": [],
},

"31 CFR 1020.220": {
 "url": "https://www.ecfr.gov/current/title-31/subtitle-B/chapter-X/part-1020/subpart-B/section-1020.220",
 "text": """(a) Customer Identification Program: minimum requirements. (1) In general. A bank required to have an anti-money laundering compliance program under the regulations implementing 31 U.S.C. 5318(h) must implement a written Customer Identification Program (CIP) appropriate for the bank's size and type of business that, at a minimum, includes each of the requirements of paragraphs (a)(1) through (5) of this section. The CIP must be a part of the bank's anti-money laundering compliance program required under 31 U.S.C. 5318(h). (2) Identity verification procedures. The CIP must include risk-based procedures for verifying the identity of each customer to the extent reasonable and practicable. The procedures must enable the bank to form a reasonable belief that it knows the true identity of each customer. (3) Recordkeeping. The CIP must include procedures for making and maintaining a record of all information obtained under the procedures implementing paragraph (a) of this section. (4) Comparison with government lists. The CIP must include procedures for determining whether the customer appears on any list of known or suspected terrorists or terrorist organizations issued by any Federal government agency and designated as such by Treasury in consultation with the Federal functional regulators. (5) Customer notice. The CIP must include procedures for providing bank customers with adequate notice that the bank is requesting information to verify their identities.""",
 "expected_duties": 6,
 "expected_paths": ["(a) (1)", "(a) (2)", "(a) (3)", "(a) (4)", "(a) (5)"],
 "expected_evidence": {"policy document", "account opening file", "screening log", "customer record"},
 "expected_numbers": [],
},

"31 CFR 1020.320": {
 "url": "https://www.ecfr.gov/current/title-31/subtitle-B/chapter-X/part-1020/subpart-C/section-1020.320",
 "text": """(a)(2) A transaction requires reporting under the terms of this section if it is conducted or attempted by, at, or through the bank, it involves or aggregates at least $5,000 in funds or other assets, and the bank knows, suspects, or has reason to suspect that the transaction involves funds derived from illegal activity, is designed to evade any requirement of this chapter, has no business or apparent lawful purpose, or involves use of the bank to facilitate criminal activity. (b)(3) Filing procedures. A bank is required to file a SAR no later than 30 calendar days after the date of initial detection by the bank of facts that may constitute a basis for filing a SAR. If no suspect was identified on the date of the detection of the incident requiring the filing, a bank may delay filing a SAR for an additional 30 calendar days to identify a suspect. In no case shall reporting be delayed more than 60 calendar days after the date of initial detection of a reportable transaction. (d) Retention of records. A bank shall maintain a copy of any SAR filed and the original or business record equivalent of any supporting documentation for a period of five years from the date of filing the SAR. (e) Confidentiality of SARs. A SAR, and any information that would reveal the existence of a SAR, are confidential and shall not be disclosed except as authorized in this paragraph (e).""",
 "expected_duties": 4,
 "expected_paths": ["(a) (2)", "(b) (3)", "(d)", "(e)"],
 "expected_evidence": {"filing record", "archive index", "access log", "case file"},
 "expected_numbers": [(5000.0, "usd"), (30, "calendar_days"),
                      (60, "calendar_days"), (5, "years")],
},

"31 CFR 1010.410": {
 "url": "https://www.ecfr.gov/current/title-31/subtitle-B/chapter-X/part-1010/subpart-D/section-1010.410",
 "text": """(a) A record of each extension of credit in an amount in excess of $10,000, except an extension of credit secured by an interest in real property, which record shall contain the name and address of the person to whom the extension of credit is made, the amount thereof, the nature or purpose thereof, and the date thereof. (e) Nonbank financial institutions. Each agent, agency, branch, or office located within the United States of a financial institution other than a bank is subject to the requirements of this paragraph (e) with respect to a transmittal of funds in the amount of $3,000 or more. (f) Any transmittor's financial institution or intermediary financial institution located within the United States shall include in any transmittal order for a transmittal of funds in the amount of $3,000 or more, information as required in this paragraph (f): (1) A transmittor's financial institution shall include in a transmittal order, at the time it is sent to a receiving financial institution, the following information: (i) The name and, if the payment is ordered from an account, the account number of the transmittor; (ii) The address of the transmittor; (iii) The amount of the transmittal order; (iv) The execution date of the transmittal order; and (v) The identity of the recipient's financial institution.""",
 "expected_duties": 7,
 "expected_paths": ["(a)", "(f)", "(f) (1) (i)", "(f) (1) (ii)",
                    "(f) (1) (iii)", "(f) (1) (iv)", "(f) (1) (v)"],
 "expected_evidence": {"wire message", "loan file"},
 "expected_numbers": [(10000.0, "usd"), (3000.0, "usd")],
 "trap": "Paragraph (e) is limited to non-banks. Paragraph (f) is not. A rule "
         "that qualifies the $3,000 threshold as non-bank-only is the defect "
         "this whole pipeline exists to catch.",
},

"12 CFR 748.1": {
 "url": "https://www.ecfr.gov/current/title-12/chapter-VII/subchapter-A/part-748/section-748.1",
 "text": """(b) Catastrophic act report. Each federally insured credit union will notify the regional director within 5 business days of any catastrophic act that occurs at its office(s). (c) Cyber incident report. The NCUA must receive this notification as soon as possible but no later than 72 hours after a federally insured credit union reasonably believes that it has experienced a reportable cyber incident.""",
 "expected_duties": 2,
 "expected_paths": ["(b)", "(c)"],
 "expected_evidence": {"notification record"},
 "expected_numbers": [(5, "business_days"), (72, "hours")],
 "trap": "Charter flips here. Part 748 is NCUA — credit union only. A rule "
         "marked applies_bank=Y is wrong, which is the mirror of the "
         "1010.100(d) default.",
},
}
