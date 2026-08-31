"""
gdpr_data.py
------------
Static chapter map + article titles for the EU General Data Protection
Regulation (Regulation (EU) 2016/679), mirroring dpdp_config.py's
CHAPTER_MAP / SECTION_TITLES / chapter_for_section() pattern exactly, so
gdpr_ingest.py can reuse the same "sequential-anchor split + known title
lookup" approach that already works for the DPDP Act.

Titles and chapter boundaries transcribed from the official consolidated
text (source: eur-lex.europa.eu, CELEX:32016R0679). This is the FULL
regulation - 99 Articles across 11 Chapters - per the user's request for
the complete GDPR text, not a curated subset.
"""

MAX_ARTICLE = 99

CHAPTER_MAP = [
    ("1", "GENERAL PROVISIONS", 1, 4),
    ("2", "PRINCIPLES", 5, 11),
    ("3", "RIGHTS OF THE DATA SUBJECT", 12, 23),
    ("4", "CONTROLLER AND PROCESSOR", 24, 43),
    ("5", "TRANSFERS OF PERSONAL DATA TO THIRD COUNTRIES OR INTERNATIONAL ORGANISATIONS", 44, 50),
    ("6", "INDEPENDENT SUPERVISORY AUTHORITIES", 51, 59),
    ("7", "COOPERATION AND CONSISTENCY", 60, 76),
    ("8", "REMEDIES, LIABILITY AND PENALTIES", 77, 84),
    ("9", "PROVISIONS RELATING TO SPECIFIC PROCESSING SITUATIONS", 85, 91),
    ("10", "DELEGATED ACTS AND IMPLEMENTING ACTS", 92, 93),
    ("11", "FINAL PROVISIONS", 94, 99),
]

ARTICLE_TITLES = {
    1: "Subject-matter and objectives", 2: "Material scope", 3: "Territorial scope",
    4: "Definitions",
    5: "Principles relating to processing of personal data", 6: "Lawfulness of processing",
    7: "Conditions for consent",
    8: "Conditions applicable to child's consent in relation to information society services",
    9: "Processing of special categories of personal data",
    10: "Processing of personal data relating to criminal convictions and offences",
    11: "Processing which does not require identification",
    12: "Transparent information, communication and modalities for the exercise of the rights of the data subject",
    13: "Information to be provided where personal data are collected from the data subject",
    14: "Information to be provided where personal data have not been obtained from the data subject",
    15: "Right of access by the data subject", 16: "Right to rectification",
    17: "Right to erasure ('right to be forgotten')", 18: "Right to restriction of processing",
    19: "Notification obligation regarding rectification or erasure of personal data or restriction of processing",
    20: "Right to data portability", 21: "Right to object",
    22: "Automated individual decision-making, including profiling", 23: "Restrictions",
    24: "Responsibility of the controller", 25: "Data protection by design and by default",
    26: "Joint controllers",
    27: "Representatives of controllers or processors not established in the Union",
    28: "Processor", 29: "Processing under the authority of the controller or processor",
    30: "Records of processing activities", 31: "Cooperation with the supervisory authority",
    32: "Security of processing",
    33: "Notification of a personal data breach to the supervisory authority",
    34: "Communication of a personal data breach to the data subject",
    35: "Data protection impact assessment", 36: "Prior consultation",
    37: "Designation of the data protection officer",
    38: "Position of the data protection officer", 39: "Tasks of the data protection officer",
    40: "Codes of conduct", 41: "Monitoring of approved codes of conduct",
    42: "Certification", 43: "Certification bodies",
    44: "General principle for transfers", 45: "Transfers on the basis of an adequacy decision",
    46: "Transfers subject to appropriate safeguards", 47: "Binding corporate rules",
    48: "Transfers or disclosures not authorised by Union law",
    49: "Derogations for specific situations",
    50: "International cooperation for the protection of personal data",
    51: "Supervisory authority", 52: "Independence",
    53: "General conditions for the members of the supervisory authority",
    54: "Rules on the establishment of the supervisory authority", 55: "Competence",
    56: "Competence of the lead supervisory authority", 57: "Tasks", 58: "Powers",
    59: "Activity reports",
    60: "Cooperation between the lead supervisory authority and the other supervisory authorities concerned",
    61: "Mutual assistance", 62: "Joint operations of supervisory authorities",
    63: "Consistency mechanism", 64: "Opinion of the Board", 65: "Dispute resolution by the Board",
    66: "Urgency procedure", 67: "Exchange of information", 68: "European Data Protection Board",
    69: "Independence (Board)", 70: "Tasks of the Board", 71: "Reports", 72: "Procedure",
    73: "Chair", 74: "Tasks of the Chair", 75: "Secretariat", 76: "Confidentiality",
    77: "Right to lodge a complaint with a supervisory authority",
    78: "Right to an effective judicial remedy against a supervisory authority",
    79: "Right to an effective judicial remedy against a controller or processor",
    80: "Representation of data subjects", 81: "Suspension of proceedings",
    82: "Right to compensation and liability",
    83: "General conditions for imposing administrative fines", 84: "Penalties",
    85: "Processing and freedom of expression and information",
    86: "Processing and public access to official documents",
    87: "Processing of the national identification number",
    88: "Processing in the context of employment",
    89: "Safeguards and derogations relating to processing for archiving purposes in the public "
        "interest, scientific or historical research purposes or statistical purposes",
    90: "Obligations of secrecy",
    91: "Existing data protection rules of churches and religious associations",
    92: "Exercise of the delegation", 93: "Committee procedure",
    94: "Repeal of Directive 95/46/EC", 95: "Relationship with Directive 2002/58/EC",
    96: "Relationship with previously concluded Agreements", 97: "Commission reports",
    98: "Review of other Union legal acts on data protection",
    99: "Entry into force and application",
}


def chapter_for_article(article_number: int) -> str:
    for number, title, start, end in CHAPTER_MAP:
        if start <= article_number <= end:
            return f"Chapter {number} - {title}"
    return "Unknown"
